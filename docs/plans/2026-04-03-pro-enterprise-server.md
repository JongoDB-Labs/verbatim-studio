# Pro/Enterprise Server Deployment — Implementation Plan

**Issue:** #129
**Timeline:** May–July 2026
**Goal:** Deploy Verbatim's backend as a multi-user server on Apple Silicon (MLX) and NVIDIA CUDA, with PostgreSQL for concurrent access.

---

## Architecture

```
                    ┌─────────────────────┐
                    │    Nginx / Caddy    │
                    │  (reverse proxy,    │
                    │   TLS termination)  │
                    └────────┬────────────┘
                             │
                    ┌────────▼────────────┐
                    │  FastAPI + Uvicorn  │
                    │  (multi-worker)     │
                    │                     │
                    │  Auth middleware     │
                    │  Rate limiting      │
                    │  Usage metering     │
                    └────┬───────┬────────┘
                         │       │
              ┌──────────▼┐  ┌──▼──────────────┐
              │ Whisper   │  │ LLM Serving      │
              │ Engine    │  │                  │
              │           │  │ Apple Silicon:   │
              │ MLX:      │  │   mlx-lm /       │
              │  mlx-     │  │   vllm-mlx       │
              │  whisper  │  │                  │
              │           │  │ NVIDIA:          │
              │ NVIDIA:   │  │   vLLM           │
              │  faster-  │  │                  │
              │  whisper  │  │ (OpenAI-compat   │
              └──────┬────┘  │  API endpoint)   │
                     │       └────────┬─────────┘
                     │                │
              ┌──────▼────────────────▼─────────┐
              │     PostgreSQL + pgvector        │
              │     (replaces SQLite)            │
              └─────────────────────────────────┘
```

---

## Phase 1: PostgreSQL Migration (Weeks 1-2)

The enterprise tier already has partial PostgreSQL support via the adapter pattern. Complete it.

### Step 1: Verify existing adapter

```python
# core/config.py already supports:
DATABASE_URL: str = "sqlite+aiosqlite:///./verbatim.db"
# Change to:
DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost/verbatim"
```

**Check:** Run all existing tests against PostgreSQL to find SQLite-specific queries.

### Step 2: Replace sqlite-vec with pgvector

```sql
CREATE EXTENSION vector;

-- Migrate embedding columns
ALTER TABLE segment_embeddings
  ADD COLUMN embedding_pgvec vector(768);
```

**New adapter:** `packages/backend/services/search/pgvector_search.py`
```python
from pgvector.sqlalchemy import Vector

class PgVectorSearchService:
    async def find_similar(self, query_embedding, limit=10):
        result = await db.execute(
            select(SegmentEmbedding)
            .order_by(SegmentEmbedding.embedding_pgvec.cosine_distance(query_embedding))
            .limit(limit)
        )
        return result.scalars().all()
```

### Step 3: Alembic migration system

Replace manual migration scripts with Alembic for proper version tracking:

```bash
pip install alembic
alembic init migrations
alembic revision --autogenerate -m "initial schema"
```

### Step 4: Connection pooling

```python
# For server mode: connection pooling
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
)
```

---

## Phase 2: Multi-Worker Server Mode (Weeks 2-3)

### Step 1: Server entry point

**File:** `packages/backend/server.py`

```python
"""Production server entry point for Verbatim Pro/Enterprise."""

import uvicorn
from core.config import settings

def main():
    uvicorn.run(
        "api.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        workers=settings.SERVER_WORKERS,
        ssl_keyfile=settings.SSL_KEY,
        ssl_certfile=settings.SSL_CERT,
        log_level="info",
    )

if __name__ == "__main__":
    main()
```

### Step 2: Config additions

```python
# core/config.py additions
SERVER_HOST: str = "0.0.0.0"
SERVER_PORT: int = 52780
SERVER_WORKERS: int = 4  # CPU cores for request handling
SERVER_MODE: str = "desktop"  # "desktop" | "server" | "enterprise"
SSL_KEY: str | None = None
SSL_CERT: str | None = None

# Auth (server mode only)
AUTH_ENABLED: bool = False
AUTH_SECRET_KEY: str = ""
AUTH_ALGORITHM: str = "HS256"
```

### Step 3: Authentication middleware

**File:** `packages/backend/api/middleware/auth.py`

```python
from fastapi import Request, HTTPException
from jose import jwt

async def auth_middleware(request: Request, call_next):
    if not settings.AUTH_ENABLED:
        return await call_next(request)

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "Authentication required")

    try:
        payload = jwt.decode(token, settings.AUTH_SECRET_KEY, algorithms=[settings.AUTH_ALGORITHM])
        request.state.user_id = payload["sub"]
        request.state.user_role = payload.get("role", "user")
    except jwt.JWTError:
        raise HTTPException(401, "Invalid token")

    return await call_next(request)
```

### Step 4: Usage metering

Track per-user consumption for billing:

```python
# New model: UsageRecord
class UsageRecord(Base):
    __tablename__ = "usage_records"
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    action = Column(String)  # "transcribe", "chat", "search", "export"
    audio_seconds = Column(Float, default=0)
    llm_tokens = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
```

---

## Phase 3: Apple Silicon MLX Optimization (Weeks 3-4)

### Target hardware: M3 128GB MacBook Pro (dev/test), Mac Mini M4 Pro (production)

### Step 1: MLX Whisper (already integrated)

Your `pyproject.toml` already has `mlx-whisper>=0.4.0`. For server mode, add:

**Batched inference** via Lightning Whisper MLX:
```bash
pip install lightning-whisper-mlx
```

```python
from lightning_whisper_mlx import LightningWhisperMLX

whisper = LightningWhisperMLX(model="distil-large-v3", batch_size=12, quant=None)
result = whisper.transcribe("/path/to/audio.mp3")
```

This gives ~4x speedup over standard mlx-whisper through batched decoding.

### Step 2: LLM serving with mlx-lm

Replace llama-cpp-python with mlx-lm for Apple Silicon server:

```bash
pip install mlx-lm
# Start OpenAI-compatible server
mlx_lm.server --model mlx-community/granite-3.1-8b-instruct-4bit --port 8081
```

**Adapter:** `packages/backend/adapters/mlx_lm_adapter.py`
```python
"""MLX-LM adapter using OpenAI-compatible API."""

import httpx

class MLXLMAdapter:
    def __init__(self, base_url="http://localhost:8081"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url)

    async def chat(self, messages, options):
        response = await self.client.post("/v1/chat/completions", json={
            "model": "granite-8b",
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
            "stream": False,
        })
        data = response.json()
        return ChatResponse(content=data["choices"][0]["message"]["content"])

    async def chat_stream(self, messages, options):
        async with self.client.stream("POST", "/v1/chat/completions", json={
            "model": "granite-8b",
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
            "stream": True,
        }) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        yield ChatChunk(content=delta["content"])
```

### Step 3: Capacity planning

**M3 128GB unified memory budget:**

| Component | Memory | Instances |
|-----------|--------|-----------|
| mlx-whisper large-v3 | ~2.5GB each | Up to 10 concurrent |
| Granite 8B (4-bit via mlx-lm) | ~5GB | 1 server instance |
| Embedding model | ~1GB | 1 instance |
| PostgreSQL | ~2GB | 1 instance |
| FastAPI workers | ~200MB each | 4 workers |
| OS + headroom | ~8GB | — |
| **Total** | ~42GB active | **~86GB free** |

Estimate: **10-15 concurrent transcription users + 20-50 chat users** on a single M3 128GB machine.

---

## Phase 4: NVIDIA CUDA Optimization (Weeks 4-5)

### Target hardware: Windows/Linux with 64GB VRAM

### Step 1: faster-whisper (already supported via CTranslate2)

```python
from faster_whisper import WhisperModel, BatchedInferencePipeline

model = WhisperModel("large-v3", device="cuda", compute_type="int8")
batched = BatchedInferencePipeline(model=model)
segments, info = batched.transcribe("audio.mp3", batch_size=16)
```

### Step 2: vLLM for LLM serving

```bash
pip install vllm
# Start server
python -m vllm.entrypoints.openai.api_server \
  --model ibm-granite/granite-3.1-8b-instruct \
  --dtype float16 \
  --max-model-len 8192 \
  --port 8081
```

Same `MLXLMAdapter` works since vLLM exposes OpenAI-compatible API.

### Step 3: VRAM budget (64GB)

| Component | VRAM | Notes |
|-----------|------|-------|
| Whisper large-v3 (INT8) | ~1.5GB | Multiple instances feasible |
| Granite 8B (FP16) | ~16GB | Via vLLM with PagedAttention |
| KV cache (8 concurrent) | ~4GB | vLLM manages automatically |
| Embedding model | ~1GB | sentence-transformers on GPU |
| **Total active** | ~23GB | **~41GB free for scaling** |

Can scale to larger models: Llama 3.1 70B Q4 (~38GB) replaces Granite 8B for better quality.

### Step 4: Docker deployment

**`docker/Dockerfile.cuda`:**
```dockerfile
FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3.12 python3-pip ffmpeg
COPY packages/backend/ /app/
RUN pip install -r /app/requirements-cuda.txt

EXPOSE 52780
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "52780", "--workers", "4"]
```

```bash
docker run --gpus all -p 52780:52780 \
  -e DATABASE_URL=postgresql+asyncpg://... \
  -e SERVER_MODE=enterprise \
  verbatim-studio:cuda
```

---

## Phase 5: Admin Dashboard & Multi-Tenancy (Weeks 5-8)

### Admin endpoints

```
GET  /api/admin/users          — list users, roles
POST /api/admin/users          — create user
GET  /api/admin/usage          — usage statistics
GET  /api/admin/health         — server health (GPU util, memory, queue depth)
POST /api/admin/models/reload  — hot-reload ML models
```

### Multi-tenancy

- Users see only their own projects/recordings
- Admin users see everything
- Filter all queries by `user_id` in server mode
- Existing `project_id` scoping extends naturally to `user_id` scoping

### Queue management

For server mode, replace in-process job queue with Redis:

```python
# Already have: services/jobs.py
# Add Redis backend option:
if settings.SERVER_MODE == "enterprise":
    from redis import asyncio as aioredis
    redis = aioredis.from_url(settings.REDIS_URL)
    job_queue = RedisJobQueue(redis)
else:
    job_queue = SQLiteJobQueue(db)
```

---

## Deployment Configurations

### Development (your hardware)

**Apple Silicon (M3 128GB MacBook Pro):**
```bash
# Terminal 1: MLX LLM server
mlx_lm.server --model mlx-community/granite-3.1-8b-instruct-4bit --port 8081

# Terminal 2: Verbatim backend
DATABASE_URL=postgresql+asyncpg://localhost/verbatim \
SERVER_MODE=server \
LLM_BASE_URL=http://localhost:8081 \
python -m uvicorn api.main:app --host 0.0.0.0 --port 52780 --workers 4
```

**NVIDIA (Windows 64GB VRAM):**
```bash
# Terminal 1: vLLM server
python -m vllm.entrypoints.openai.api_server --model ibm-granite/granite-3.1-8b-instruct --port 8081

# Terminal 2: Verbatim backend
set DATABASE_URL=postgresql+asyncpg://localhost/verbatim
set SERVER_MODE=server
set LLM_BASE_URL=http://localhost:8081
uvicorn api.main:app --host 0.0.0.0 --port 52780 --workers 4
```

### Production Colocation

| Provider | Hardware | Monthly Cost | Best For |
|----------|----------|-------------|----------|
| MacStadium | Mac Mini M4 Pro | ~$100-200/mo | Apple Silicon production |
| Scaleway | Mac Mini cluster | ~$80-150/mo each | High-density EU |
| GPU Cloud (Lambda, RunPod) | A100 80GB | ~$1-2/hr | NVIDIA on-demand |
| Self-hosted | Your hardware | Electricity only | Dev/test |

---

## Pricing Implementation

### License key system

```python
# Simple JWT-based license
class LicenseValidator:
    async def validate(self, license_key: str) -> LicenseInfo:
        payload = jwt.decode(license_key, PUBLIC_KEY, algorithms=["RS256"])
        return LicenseInfo(
            tier=payload["tier"],        # "pro" | "team" | "enterprise"
            max_users=payload["seats"],
            expires_at=payload["exp"],
            features=payload["features"],
        )
```

### Feature gating

```python
# Middleware that checks license tier
@app.middleware("http")
async def license_check(request, call_next):
    if settings.SERVER_MODE == "desktop":
        return await call_next(request)  # Desktop = all features

    license = request.state.license
    endpoint = request.url.path

    if endpoint.startswith("/api/admin") and license.tier != "enterprise":
        raise HTTPException(403, "Enterprise license required")

    return await call_next(request)
```

---

## Testing Plan

1. **PostgreSQL migration:** Run full test suite against PostgreSQL (CI: add postgres service)
2. **Multi-worker:** Load test with locust (10/50/100 concurrent users)
3. **MLX throughput:** Benchmark transcription throughput on M3 128GB
4. **CUDA throughput:** Benchmark on 64GB VRAM machine
5. **Auth flow:** End-to-end JWT auth with role-based access
6. **Usage metering accuracy:** Verify audio seconds and token counts match
