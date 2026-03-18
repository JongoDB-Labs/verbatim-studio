"""Configuration and status endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.ai_settings import (
    CONTEXT_RAM_ESTIMATES,
    VALID_CONTEXT_SIZES,
    get_ai_settings,
    save_ai_settings,
)
from core.config import settings
from core.model_catalog import MODEL_CATALOG
from core.oauth_credentials import (
    OAUTH_PROVIDERS,
    PROVIDER_INFO,
    delete_oauth_credentials,
    get_oauth_credentials,
    save_oauth_credentials,
)
from core.transcription_settings import (
    PRESETS,
    VALID_BATCH_SIZES,
    VALID_COMPUTE_TYPES,
    VALID_DEVICES,
    VALID_ENGINES,
    VALID_MODELS,
    detect_available_devices,
    detect_optimal_engine,
    get_available_engines,
    get_devices_for_engine,
    get_transcription_settings,
    is_apple_silicon,
    is_mlx_whisper_available,
    save_transcription_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


class WhisperXStatus(BaseModel):
    """WhisperX configuration status."""

    mode: str  # "local" or "external"
    external_url: str | None
    model: str
    device: str
    compute_type: str


class AIStatus(BaseModel):
    """AI configuration status."""

    model_path: str | None
    context_size: int
    gpu_layers: int


class ConfigStatus(BaseModel):
    """Overall configuration status."""

    mode: str  # "basic" or "enterprise"
    whisperx: WhisperXStatus
    ai: AIStatus


@router.get("/status", response_model=ConfigStatus)
async def get_config_status() -> ConfigStatus:
    """Get current configuration status.

    Returns information about the current configuration including
    WhisperX mode (local vs external) and AI settings.
    """
    whisperx_mode = "external" if settings.WHISPERX_EXTERNAL_URL else "local"

    return ConfigStatus(
        mode=settings.MODE,
        whisperx=WhisperXStatus(
            mode=whisperx_mode,
            external_url=settings.WHISPERX_EXTERNAL_URL,
            model=settings.WHISPERX_MODEL,
            device=settings.WHISPERX_DEVICE,
            compute_type=settings.WHISPERX_COMPUTE_TYPE,
        ),
        ai=AIStatus(
            model_path=settings.AI_MODEL_PATH,
            context_size=settings.AI_N_CTX,
            gpu_layers=settings.AI_N_GPU_LAYERS,
        ),
    )


# --- AI Settings ---


class AISettingsResponse(BaseModel):
    context_size: int
    available_context_sizes: list[int]
    max_model_context: int
    ram_estimates: dict[int, str]


class AISettingsUpdate(BaseModel):
    context_size: int | None = None


async def _get_ai_config() -> AISettingsResponse:
    """Build AISettingsResponse from current state."""
    from api.routes.ai import _read_active_model

    effective = await get_ai_settings()
    active_model = _read_active_model()
    max_context = 131072
    if active_model and active_model in MODEL_CATALOG:
        max_context = MODEL_CATALOG[active_model].get("max_context", 131072)

    available_sizes = [s for s in VALID_CONTEXT_SIZES if s <= max_context]

    ram_estimates = CONTEXT_RAM_ESTIMATES  # default fallback
    if active_model and active_model in MODEL_CATALOG:
        model_ram = MODEL_CATALOG[active_model].get("ram_estimates")
        if model_ram:
            ram_estimates = model_ram

    return AISettingsResponse(
        context_size=effective["context_size"],
        available_context_sizes=available_sizes,
        max_model_context=max_context,
        ram_estimates=ram_estimates,
    )


@router.get("/ai", response_model=AISettingsResponse)
async def get_ai_config() -> AISettingsResponse:
    """Get effective AI settings and available options."""
    return await _get_ai_config()


@router.put("/ai", response_model=AISettingsResponse)
async def update_ai_config(body: AISettingsUpdate) -> AISettingsResponse:
    """Update AI settings. Only provided fields are changed."""
    updates: dict[str, Any] = {}

    if body.context_size is not None:
        if body.context_size not in VALID_CONTEXT_SIZES:
            raise HTTPException(
                400,
                f"Invalid context_size: {body.context_size}. Must be one of: {VALID_CONTEXT_SIZES}",
            )
        updates["context_size"] = body.context_size

    if not updates:
        raise HTTPException(400, "No valid fields provided")

    await save_ai_settings(updates)

    # Update runtime setting
    if "context_size" in updates:
        old_ctx = settings.AI_N_CTX
        settings.AI_N_CTX = updates["context_size"]
        logger.info(
            "Context window changed: %dK -> %dK tokens. Unloading model (will reload on next request).",
            old_ctx // 1024, updates["context_size"] // 1024,
        )

    # Force model reload to pick up new context size
    from adapters.ai.llama_cpp import cleanup_llama_service
    cleanup_llama_service()

    return await _get_ai_config()


# --- Transcription Settings ---


class PresetInfo(BaseModel):
    """Preset configuration card info."""

    model: str
    compute_type: str
    batch_size: int


class TranscriptionSettingsResponse(BaseModel):
    """Effective transcription settings + capabilities."""

    # Engine selection
    engine: str  # Current setting: auto, whisperx, mlx-whisper
    effective_engine: str  # Resolved engine after auto-detection
    available_engines: list[str]  # What's actually available on this system
    engine_caveats: list[str]  # Warnings to display in UI

    # Effective values
    model: str
    device: str
    compute_type: str
    batch_size: int
    diarize: bool
    hf_token_set: bool
    hf_token_masked: str | None

    # External mode info (enterprise feature)
    mode: str  # "local" or "external"
    external_url: str | None
    external_api_key_set: bool
    external_api_key_masked: str | None
    is_enterprise: bool  # Whether enterprise mode is enabled

    # Available options for UI dropdowns
    available_models: list[str]
    available_devices: list[str]
    available_compute_types: list[str]
    available_batch_sizes: list[int]

    # Presets
    presets: dict[str, PresetInfo]


class TranscriptionSettingsUpdate(BaseModel):
    """Partial update for transcription settings."""

    engine: str | None = None
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None
    batch_size: int | None = None
    diarize: bool | None = None
    hf_token: str | None = None
    # External WhisperX service (enterprise feature)
    external_url: str | None = None
    external_api_key: str | None = None


def _mask_token(token: str | None) -> str | None:
    if not token:
        return None
    if len(token) <= 4:
        return "****"
    return "****" + token[-4:]


def _get_engine_caveats(engine: str, effective_engine: str, diarize: bool) -> list[str]:
    """Generate caveats/warnings for the current engine configuration."""
    caveats = []

    # User selected mlx-whisper but not on Apple Silicon
    if engine == "mlx-whisper" and not is_apple_silicon():
        caveats.append(
            "MLX Whisper requires Apple Silicon. Will fall back to WhisperX."
        )

    # User selected whisperx but mlx-whisper is available (tip)
    if (
        effective_engine == "whisperx"
        and is_apple_silicon()
        and is_mlx_whisper_available()
    ):
        caveats.append(
            "Tip: MLX Whisper provides faster GPU-accelerated transcription on Apple Silicon."
        )

    return caveats


def _build_response(effective: dict[str, Any]) -> TranscriptionSettingsResponse:
    hf_token = effective.get("hf_token")
    external_url = effective.get("external_url")
    external_api_key = effective.get("external_api_key")

    # Resolve engine
    engine = effective.get("engine", "auto")
    if engine == "auto":
        effective_engine = detect_optimal_engine()
    else:
        effective_engine = engine

    # Get available engines and caveats
    available_engines = get_available_engines()
    engine_caveats = _get_engine_caveats(engine, effective_engine, effective["diarize"])

    # Get available devices based on effective engine
    # This helps the UI show only relevant device options
    available_devices = detect_available_devices()

    # Determine mode based on effective settings (DB or env var)
    mode = "external" if external_url else "local"

    return TranscriptionSettingsResponse(
        engine=engine,
        effective_engine=effective_engine,
        available_engines=available_engines,
        engine_caveats=engine_caveats,
        model=effective["model"],
        device=effective["device"],
        compute_type=effective["compute_type"],
        batch_size=effective["batch_size"],
        diarize=effective["diarize"],
        hf_token_set=bool(hf_token),
        hf_token_masked=_mask_token(hf_token),
        mode=mode,
        external_url=external_url,
        external_api_key_set=bool(external_api_key),
        external_api_key_masked=_mask_token(external_api_key),
        is_enterprise=settings.MODE == "enterprise",
        available_models=VALID_MODELS,
        available_devices=available_devices,
        available_compute_types=VALID_COMPUTE_TYPES,
        available_batch_sizes=VALID_BATCH_SIZES,
        presets={k: PresetInfo(**v) for k, v in PRESETS.items()},
    )


@router.get("/transcription", response_model=TranscriptionSettingsResponse)
async def get_transcription_config() -> TranscriptionSettingsResponse:
    """Get effective transcription settings and available options."""
    effective = await get_transcription_settings()
    return _build_response(effective)


@router.put("/transcription", response_model=TranscriptionSettingsResponse)
async def update_transcription_config(
    body: TranscriptionSettingsUpdate,
) -> TranscriptionSettingsResponse:
    """Update transcription settings. Only provided fields are changed."""
    updates: dict[str, Any] = {}

    if body.engine is not None:
        if body.engine not in VALID_ENGINES:
            raise HTTPException(
                400, f"Invalid engine: {body.engine}. Must be one of: {VALID_ENGINES}"
            )
        updates["engine"] = body.engine

    if body.model is not None:
        if body.model not in VALID_MODELS:
            raise HTTPException(400, f"Invalid model: {body.model}. Must be one of: {VALID_MODELS}")
        updates["model"] = body.model

    if body.device is not None:
        if body.device not in VALID_DEVICES:
            raise HTTPException(400, f"Invalid device: {body.device}. Must be one of: {VALID_DEVICES}")
        updates["device"] = body.device

    if body.compute_type is not None:
        if body.compute_type not in VALID_COMPUTE_TYPES:
            raise HTTPException(400, f"Invalid compute_type: {body.compute_type}. Must be one of: {VALID_COMPUTE_TYPES}")
        updates["compute_type"] = body.compute_type

    if body.batch_size is not None:
        if body.batch_size not in VALID_BATCH_SIZES:
            raise HTTPException(400, f"Invalid batch_size: {body.batch_size}. Must be one of: {VALID_BATCH_SIZES}")
        updates["batch_size"] = body.batch_size

    if body.diarize is not None:
        updates["diarize"] = body.diarize

    if body.hf_token is not None:
        # Allow empty string to clear the token
        updates["hf_token"] = body.hf_token if body.hf_token else None

    # External WhisperX settings (enterprise feature)
    if body.external_url is not None:
        # Allow empty string to clear the URL
        updates["external_url"] = body.external_url if body.external_url else None

    if body.external_api_key is not None:
        # Allow empty string to clear the key
        updates["external_api_key"] = body.external_api_key if body.external_api_key else None

    if not updates:
        raise HTTPException(400, "No valid fields provided")

    await save_transcription_settings(updates)
    effective = await get_transcription_settings()
    return _build_response(effective)


# --- Web Search Settings ---


class WebSearchSettingsResponse(BaseModel):
    provider: str
    api_key_set: bool
    api_key_masked: str | None


class WebSearchSettingsUpdate(BaseModel):
    provider: str | None = None
    api_key: str | None = None  # Empty string to clear


@router.get("/web-search", response_model=WebSearchSettingsResponse)
async def get_web_search_settings() -> WebSearchSettingsResponse:
    """Get web search configuration."""
    from sqlalchemy import select

    from persistence.database import get_session_factory
    from persistence.models import Setting
    from services.encryption import decrypt_config

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "web_search_settings")
        )
        setting = result.scalar_one_or_none()

    config: dict = {}
    if setting and setting.value:
        config = decrypt_config(setting.value) if isinstance(setting.value, dict) else {}

    # Fallback to env vars
    provider = config.get("provider") or settings.WEB_SEARCH_PROVIDER or "tavily"
    api_key = config.get("api_key") or settings.WEB_SEARCH_API_KEY

    return WebSearchSettingsResponse(
        provider=provider,
        api_key_set=bool(api_key),
        api_key_masked=_mask_token(api_key),
    )


@router.put("/web-search", response_model=WebSearchSettingsResponse)
async def update_web_search_settings(body: WebSearchSettingsUpdate) -> WebSearchSettingsResponse:
    """Update web search configuration."""
    from sqlalchemy import select

    from persistence.database import get_session_factory
    from persistence.models import Setting
    from services.encryption import decrypt_config, encrypt_config

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "web_search_settings")
        )
        setting = result.scalar_one_or_none()

        current: dict = {}
        if setting and setting.value:
            current = decrypt_config(setting.value) if isinstance(setting.value, dict) else {}

        if body.provider is not None:
            current["provider"] = body.provider
        if body.api_key is not None:
            current["api_key"] = body.api_key if body.api_key else None

        encrypted = encrypt_config(current)

        if setting:
            setting.value = encrypted
        else:
            session.add(Setting(key="web_search_settings", value=encrypted))

        await session.commit()

    # Also update the runtime settings so they take effect immediately
    if body.provider is not None:
        settings.WEB_SEARCH_PROVIDER = current.get("provider")
    if body.api_key is not None:
        settings.WEB_SEARCH_API_KEY = current.get("api_key")

    return WebSearchSettingsResponse(
        provider=current.get("provider", "tavily"),
        api_key_set=bool(current.get("api_key")),
        api_key_masked=_mask_token(current.get("api_key")),
    )


# --- OAuth Credentials ---


class OAuthProviderInfo(BaseModel):
    """OAuth provider credential info."""

    client_id: str
    has_secret: bool
    configured: bool
    name: str
    setup_url: str
    docs_url: str


class OAuthCredentialsResponse(BaseModel):
    """OAuth credentials for all providers."""

    gdrive: OAuthProviderInfo | None = None
    onedrive: OAuthProviderInfo | None = None
    dropbox: OAuthProviderInfo | None = None


class OAuthCredentialsUpdate(BaseModel):
    """Update OAuth credentials for a provider."""

    client_id: str
    client_secret: str


@router.get("/oauth-credentials", response_model=OAuthCredentialsResponse)
async def get_oauth_credentials_endpoint() -> OAuthCredentialsResponse:
    """Get OAuth credentials status for all providers.

    Returns client_id and whether secret is configured (never exposes actual secret).
    """
    creds = await get_oauth_credentials()
    return OAuthCredentialsResponse(**creds)


@router.get("/oauth-credentials/{provider}", response_model=OAuthProviderInfo)
async def get_oauth_credentials_for_provider(provider: str) -> OAuthProviderInfo:
    """Get OAuth credentials status for a specific provider."""
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {provider}. Must be one of: {OAUTH_PROVIDERS}")

    creds = await get_oauth_credentials(provider)
    return OAuthProviderInfo(**creds)


@router.put("/oauth-credentials/{provider}", response_model=OAuthProviderInfo)
async def update_oauth_credentials_endpoint(
    provider: str,
    body: OAuthCredentialsUpdate,
) -> OAuthProviderInfo:
    """Set OAuth credentials for a provider."""
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {provider}. Must be one of: {OAUTH_PROVIDERS}")

    if not body.client_id or not body.client_secret:
        raise HTTPException(400, "Both client_id and client_secret are required")

    result = await save_oauth_credentials(provider, body.client_id, body.client_secret)
    return OAuthProviderInfo(**result)


@router.delete("/oauth-credentials/{provider}")
async def delete_oauth_credentials_endpoint(provider: str) -> dict[str, bool]:
    """Delete OAuth credentials for a provider."""
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {provider}. Must be one of: {OAUTH_PROVIDERS}")

    deleted = await delete_oauth_credentials(provider)
    return {"deleted": deleted}
