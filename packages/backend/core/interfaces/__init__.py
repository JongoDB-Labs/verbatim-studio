"""Core interfaces for the adapter pattern.

These interfaces define contracts that allow swapping implementations
between basic (embedded) and enterprise (external services) tiers.
"""

from .database import (
    IDatabaseAdapter,
    IJobRepository,
    IProjectRepository,
    IRecordingRepository,
    ISegmentRepository,
    ISettingRepository,
    ISpeakerRepository,
    ITranscriptRepository,
    # Domain entities
    JobEntity,
    PaginatedResult,
    ProjectEntity,
    RecordingEntity,
    SegmentEntity,
    SettingEntity,
    SpeakerEntity,
    TranscriptEntity,
)
from .transcription import (
    ITranscriptionEngine,
    TranscriptionOptions,
    TranscriptionProgress,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
)
from .diarization import (
    IDiarizationEngine,
    DiarizationOptions,
    DiarizationProgress,
    DiarizationResult,
    DiarizationSegment,
)
from .ai import (
    IAIService,
    AnalysisResult,
    ChatMessage,
    ChatOptions,
    ChatResponse,
    ChatStreamChunk,
    SummarizationResult,
)
from .auth import (
    IAuthProvider,
    AuthToken,
    LoginCredentials,
    Permission,
    RegistrationData,
    Role,
    User,
)
from .tts import (
    ITTSService,
)

__all__ = [
    # Database interfaces
    "IDatabaseAdapter",
    "IProjectRepository",
    "IRecordingRepository",
    "ITranscriptRepository",
    "ISegmentRepository",
    "ISpeakerRepository",
    "IJobRepository",
    "ISettingRepository",
    # Database entities
    "ProjectEntity",
    "RecordingEntity",
    "TranscriptEntity",
    "SegmentEntity",
    "SpeakerEntity",
    "JobEntity",
    "SettingEntity",
    "PaginatedResult",
    # Transcription
    "ITranscriptionEngine",
    "TranscriptionOptions",
    "TranscriptionProgress",
    "TranscriptionResult",
    "TranscriptionSegment",
    "TranscriptionWord",
    # Diarization
    "IDiarizationEngine",
    "DiarizationOptions",
    "DiarizationProgress",
    "DiarizationResult",
    "DiarizationSegment",
    # AI
    "IAIService",
    "ChatMessage",
    "ChatOptions",
    "ChatResponse",
    "ChatStreamChunk",
    "SummarizationResult",
    "AnalysisResult",
    # Auth
    "IAuthProvider",
    "User",
    "Role",
    "Permission",
    "AuthToken",
    "LoginCredentials",
    "RegistrationData",
    # TTS
    "ITTSService",
]
