"""SQLAlchemy models."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


class ProjectType(Base):
    """Project type template with custom metadata schema."""

    __tablename__ = "project_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_schema: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    projects: Mapped[list["Project"]] = relationship(back_populates="project_type")


class RecordingTemplate(Base):
    """Recording template with custom metadata schema."""

    __tablename__ = "recording_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_schema: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    recordings: Mapped[list["Recording"]] = relationship(back_populates="template")


class Project(Base):
    """Project model — isolated workspace for organizing content."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    project_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_types.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    icon: Mapped[str | None] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    project_type: Mapped[ProjectType | None] = relationship(back_populates="projects")
    recordings: Mapped[list["Recording"]] = relationship(back_populates="project")
    documents: Mapped[list["Document"]] = relationship(back_populates="project")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="project")


class Tag(Base):
    """Tag model for categorizing recordings."""

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    color: Mapped[str | None] = mapped_column(String(7))  # "#FF5733"
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    recordings: Mapped[list["Recording"]] = relationship(
        secondary="recording_tags", back_populates="tags"
    )
    documents: Mapped[list["Document"]] = relationship(
        secondary="document_tags", back_populates="tags"
    )


class RecordingTag(Base):
    """Junction table for recording-tag many-to-many relationship."""

    __tablename__ = "recording_tags"

    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class DocumentTag(Base):
    """Junction table for document-tag many-to-many relationship."""

    __tablename__ = "document_tags"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class ProjectRecording(Base):
    """Junction table for project-recording many-to-many relationship."""

    __tablename__ = "project_recordings"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), primary_key=True
    )


class Recording(Base):
    """Recording model for audio/video files."""

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("recording_templates.id", ondelete="SET NULL")
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL")
    )
    storage_location_id: Mapped[str | None] = mapped_column(
        ForeignKey("storage_locations.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    project: Mapped[Project | None] = relationship(back_populates="recordings")
    source: Mapped["Recording | None"] = relationship(remote_side=[id])
    storage_location: Mapped["StorageLocation | None"] = relationship()
    template: Mapped[RecordingTemplate | None] = relationship(back_populates="recordings")
    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="recording", uselist=False, cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary="recording_tags", back_populates="recordings"
    )
    notes: Mapped[list["Note"]] = relationship(back_populates="recording", cascade="all, delete-orphan")


class Transcript(Base):
    """Transcript model linked to recordings."""

    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    language: Mapped[str | None] = mapped_column(String(10))
    model_used: Mapped[str | None] = mapped_column(String(50))
    confidence_avg: Mapped[float | None] = mapped_column(Float)
    word_count: Mapped[int | None] = mapped_column(Integer)
    # AI-generated summary (summary, key_points, action_items, topics, named_entities)
    ai_summary: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    recording: Mapped[Recording] = relationship(back_populates="transcript")
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )
    speakers: Mapped[list["Speaker"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )


class Speaker(Base):
    """Speaker mapping for transcript diarization."""

    __tablename__ = "speakers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    transcript_id: Mapped[str] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    speaker_label: Mapped[str] = mapped_column(String(50), nullable=False)  # "SPEAKER_00"
    speaker_name: Mapped[str | None] = mapped_column(String(255))  # "John Smith"
    color: Mapped[str | None] = mapped_column(String(7))  # "#FF5733"

    transcript: Mapped[Transcript] = relationship(back_populates="speakers")


class Segment(Base):
    """Segment model for transcript utterances."""

    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    transcript_id: Mapped[str] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(100), index=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    edited_by: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Audit trail for vocabulary auto-corrections (Phase 2 phonetic + Phase 3 LLM).
    # Each entry: {type, original, replacement, confidence_before, edit_distance,
    # term_id, word_index}. Persisted so undo survives restart and the UI can
    # surface a ✏️ indicator on corrected words.
    corrections_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    @property
    def edited(self) -> bool:
        """Whether this segment has been edited (by human or AI)."""
        return self.edited_by is not None

    transcript: Mapped[Transcript] = relationship(back_populates="segments")
    comments: Mapped[list["SegmentComment"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )
    highlight: Mapped["SegmentHighlight | None"] = relationship(
        back_populates="segment", uselist=False, cascade="all, delete-orphan"
    )


class SegmentComment(Base):
    """Comment on a transcript segment."""

    __tablename__ = "segment_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    segment: Mapped[Segment] = relationship(back_populates="comments")


class SegmentHighlight(Base):
    """Highlight color for a transcript segment. One per segment."""

    __tablename__ = "segment_highlights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    color: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    segment: Mapped[Segment] = relationship(back_populates="highlight")


class Job(Base):
    """Job queue model for async tasks."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()


class Setting(Base):
    """Application settings model."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())


class StorageLocation(Base):
    """Configurable storage location for files (local, network, cloud)."""

    __tablename__ = "storage_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # "local", "network", "cloud"
    subtype: Mapped[str | None] = mapped_column(String(50))  # "smb", "nfs", "s3", "gdrive", etc.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="healthy")  # "healthy", "degraded", "unreachable", "auth_expired"
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())


class SegmentEmbedding(Base):
    """Embedding vector for a transcript segment."""

    __tablename__ = "segment_embeddings"

    segment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("segments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    segment: Mapped["Segment"] = relationship()


class Document(Base):
    """Document model for uploaded files (PDF, Office docs, images)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    storage_location_id: Mapped[str | None] = mapped_column(
        ForeignKey("storage_locations.id", ondelete="SET NULL")
    )

    # Processing state
    status: Mapped[str] = mapped_column(String(20), default="pending")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Extracted content
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extracted_markdown: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)

    # Metadata
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    project: Mapped["Project | None"] = relationship(back_populates="documents")
    source: Mapped["Document | None"] = relationship(remote_side=[id])
    storage_location: Mapped["StorageLocation | None"] = relationship()
    tags: Mapped[list["Tag"]] = relationship(
        secondary="document_tags", back_populates="documents"
    )
    notes: Mapped[list["Note"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Note(Base):
    """Note attached to a recording or document with contextual anchor."""

    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Polymorphic attachment (one of these set)
    recording_id: Mapped[str | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE")
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )

    # Context anchoring
    anchor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # timestamp, page, paragraph
    anchor_data: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    recording: Mapped["Recording | None"] = relationship(back_populates="notes")
    document: Mapped["Document | None"] = relationship(back_populates="notes")


class DocumentEmbedding(Base):
    """Embedding vector for a document chunk."""

    __tablename__ = "document_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column(JSON, default=dict)  # {page: 3, heading: "..."}
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    document: Mapped["Document"] = relationship()


class Conversation(Base):
    """Saved chat conversation."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    title: Mapped[str | None] = mapped_column(String(255))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    compressed_memory: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    project: Mapped["Project | None"] = relationship(back_populates="conversations")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.created_at"
    )


class ConversationMessage(Base):
    """Message in a conversation."""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class ChatWebCache(Base):
    """Cached web search results for a conversation."""

    __tablename__ = "chat_web_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    results: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    created_at: Mapped[datetime] = mapped_column(default=func.now())


class SearchHistory(Base):
    """Search history entry for tracking user searches."""

    __tablename__ = "search_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    query: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now(), index=True)


class QualityReviewRecord(Base):
    """Record of an AI quality review run on a transcript."""

    __tablename__ = "quality_review_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    transcript_id: Mapped[str] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    context_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    aggressiveness: Mapped[str] = mapped_column(String(20), default="moderate")
    corrections_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    stats_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
