"""Database connection and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    # SQLite-specific: wait up to 30s for database locks
    connect_args={"timeout": 30} if _is_sqlite else {},
)


if _is_sqlite:
    # Enable WAL mode for better concurrency (allows concurrent reads during writes)
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def get_engine():
    """Get the current database engine."""
    return engine


def set_engine(new_engine):
    """Override the database engine (called by enterprise plugin for PostgreSQL).

    This updates the module-level engine and session factory so that
    get_db() and init_db() automatically use the new engine.
    """
    global engine, async_session
    engine = new_engine
    async_session = async_sessionmaker(
        new_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def get_session_factory():
    """Get the current async session factory."""
    return async_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for database sessions."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise


async def seed_defaults(session: AsyncSession) -> None:
    """Seed or update default project types and recording templates."""
    from sqlalchemy import select

    from .defaults import DEFAULT_PROJECT_TYPES, DEFAULT_RECORDING_TEMPLATES
    from .models import ProjectType, RecordingTemplate

    # Seed/update project types
    for pt_data in DEFAULT_PROJECT_TYPES:
        result = await session.execute(
            select(ProjectType).where(ProjectType.name == pt_data["name"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Update existing system defaults with latest schema
            if existing.is_system:
                existing.description = pt_data["description"]
                existing.metadata_schema = pt_data["metadata_schema"]
        else:
            pt = ProjectType(
                name=pt_data["name"],
                description=pt_data["description"],
                metadata_schema=pt_data["metadata_schema"],
                is_system=True,
            )
            session.add(pt)

    # Seed/update recording templates
    for rt_data in DEFAULT_RECORDING_TEMPLATES:
        result = await session.execute(
            select(RecordingTemplate).where(RecordingTemplate.name == rt_data["name"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Update existing system defaults with latest schema
            if existing.is_system:
                existing.description = rt_data["description"]
                existing.metadata_schema = rt_data["metadata_schema"]
        else:
            rt = RecordingTemplate(
                name=rt_data["name"],
                description=rt_data["description"],
                metadata_schema=rt_data["metadata_schema"],
                is_system=True,
            )
            session.add(rt)

    await session.commit()


async def init_db() -> None:
    """Initialize database tables and seed defaults."""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Run schema migrations for changes that create_all won't handle
    async with engine.begin() as conn:
        await _run_migrations(conn)

    # Auto-seed defaults on startup
    async with async_session() as session:
        await seed_defaults(session)


async def _run_migrations(conn) -> None:
    """Run schema migrations that create_all doesn't handle (column drops, renames, etc)."""
    # These migrations are SQLite-specific (PRAGMA, sqlite3 module).
    # On PostgreSQL, create_all handles the full schema from models.
    if settings.DATABASE_URL.startswith("postgresql"):
        return

    # Note: Recording.project_id FK is now the standard pattern (single project per recording).
    # The project_recordings junction table is kept for backward compatibility during migration.

    # Run file browser migration (adds storage_locations table and FK columns)
    from migrations.migrate_file_browser import migrate as migrate_file_browser
    await migrate_file_browser()

    # Derive the actual database file path from the configured DATABASE_URL
    # so migrations target the correct database (including production paths
    # like ~/Library/Application Support/@verbatim/electron/verbatim.db)
    from pathlib import Path
    db_url = settings.DATABASE_URL
    # Strip the SQLAlchemy driver prefix to get the filesystem path
    # Format: sqlite+aiosqlite:///./verbatim.db or sqlite+aiosqlite:////absolute/path
    db_file = db_url.split(":///", 1)[1] if ":///" in db_url else "verbatim.db"
    db_path = Path(db_file)

    # Run storage subtype migration (adds subtype and status columns)
    # This is synchronous sqlite3, run it via run_sync
    from migrations.add_storage_subtype import migrate as migrate_storage_subtype
    await conn.run_sync(lambda _: migrate_storage_subtype(db_path))

    # Add ai_summary column to transcripts table
    from migrations.add_transcript_ai_summary import migrate as migrate_transcript_ai_summary
    await conn.run_sync(lambda _: migrate_transcript_ai_summary(db_path))

    # Add indexes on segments table for query performance
    from migrations.add_segment_indexes import migrate as migrate_segment_indexes
    await conn.run_sync(lambda _: migrate_segment_indexes(db_path))

    # Add quality review support (edited_by, original_text, quality_review_records)
    from migrations.add_quality_review import migrate as migrate_quality_review
    await conn.run_sync(lambda _: migrate_quality_review(db_path))

    # Add compressed_memory column to conversations table
    from migrations.add_conversation_compressed_memory import migrate as migrate_conversation_compressed_memory
    await conn.run_sync(lambda _: migrate_conversation_compressed_memory(db_path))

    # Add workspace columns to projects and project_id to conversations
    from migrations.add_project_workspace_columns import migrate as migrate_project_workspace
    await conn.run_sync(lambda _: migrate_project_workspace(db_path))
