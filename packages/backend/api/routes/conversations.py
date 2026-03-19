"""Conversations API routes for saved chat conversations."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_active_project_id
from api.routes.sync import broadcast
from persistence import get_db
from persistence.models import Conversation, ConversationMessage

router = APIRouter(prefix="/conversations", tags=["conversations"])


# Request/Response models
class MessageCreate(BaseModel):
    """Message to add to conversation."""

    role: str  # 'user' or 'assistant'
    content: str


class ConversationCreate(BaseModel):
    """Request to create a new conversation."""

    title: str | None = None
    messages: list[MessageCreate] = []
    compressed_memory: str | None = None
    project_id: str | None = None


class ConversationUpdate(BaseModel):
    """Request to update a conversation."""

    title: str | None = None


class MessageResponse(BaseModel):
    """Single message in a conversation."""

    id: str
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationListItem(BaseModel):
    """Conversation summary for list view."""

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    last_message_preview: str | None


class ConversationDetailResponse(BaseModel):
    """Full conversation with messages."""

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]
    compressed_memory: str | None = None

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """List of conversations."""

    items: list[ConversationListItem]
    total: int


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
) -> ConversationListResponse:
    """List all saved conversations, most recent first."""
    # Get conversations with message count
    query = (
        select(
            Conversation,
            func.count(ConversationMessage.id).label("message_count"),
        )
        .outerjoin(ConversationMessage)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
    )

    # Project scoping (from X-Active-Project header)
    if active_project_id:
        query = query.where(Conversation.project_id == active_project_id)

    result = await db.execute(query)
    rows = result.all()

    items = []
    for row in rows:
        conv = row[0]
        message_count = row[1]

        # Get last message preview
        last_message_query = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conv.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        last_msg_result = await db.execute(last_message_query)
        last_msg = last_msg_result.scalar_one_or_none()

        preview = None
        if last_msg:
            preview = last_msg.content[:100] + "..." if len(last_msg.content) > 100 else last_msg.content

        items.append(
            ConversationListItem(
                id=conv.id,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=message_count,
                last_message_preview=preview,
            )
        )

    return ConversationListResponse(items=items, total=len(items))


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationDetailResponse:
    """Get a conversation with all its messages."""
    query = (
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    result = await db.execute(query)
    conv = result.scalar_one_or_none()

    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetailResponse(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            MessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at,
            )
            for msg in sorted(conv.messages, key=lambda m: m.created_at)
        ],
        compressed_memory=conv.compressed_memory,
    )


@router.post("", response_model=ConversationDetailResponse)
async def create_conversation(
    data: ConversationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationDetailResponse:
    """Create a new conversation with optional initial messages."""
    # Generate title from first user message if not provided
    title = data.title
    if not title and data.messages:
        for msg in data.messages:
            if msg.role == "user":
                title = msg.content[:50] + "..." if len(msg.content) > 50 else msg.content
                break

    conv = Conversation(
        title=title,
        compressed_memory=data.compressed_memory,
        project_id=data.project_id,
    )
    db.add(conv)
    await db.flush()  # Get the ID

    # Add messages
    for msg_data in data.messages:
        msg = ConversationMessage(
            conversation_id=conv.id,
            role=msg_data.role,
            content=msg_data.content,
        )
        db.add(msg)

    await db.flush()

    # Reload with messages
    await db.refresh(conv)
    query = (
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conv.id)
    )
    result = await db.execute(query)
    conv = result.scalar_one()

    await broadcast("conversations", "created", str(conv.id))

    return ConversationDetailResponse(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            MessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at,
            )
            for msg in sorted(conv.messages, key=lambda m: m.created_at)
        ],
        compressed_memory=conv.compressed_memory,
    )


@router.patch("/{conversation_id}", response_model=ConversationDetailResponse)
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationDetailResponse:
    """Update conversation title."""
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if data.title is not None:
        conv.title = data.title

    await db.flush()

    # Reload with messages
    query = (
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conv.id)
    )
    result = await db.execute(query)
    conv = result.scalar_one()

    await broadcast("conversations", "updated", conversation_id)

    return ConversationDetailResponse(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            MessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at,
            )
            for msg in sorted(conv.messages, key=lambda m: m.created_at)
        ],
        compressed_memory=conv.compressed_memory,
    )


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete a conversation and all its messages."""
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.delete(conv)
    await db.flush()
    await broadcast("conversations", "deleted", conversation_id)
    return {"deleted": True}


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def add_message(
    conversation_id: str,
    data: MessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Add a message to an existing conversation."""
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = ConversationMessage(
        conversation_id=conversation_id,
        role=data.role,
        content=data.content,
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)

    await broadcast("conversations", "updated", conversation_id)

    return MessageResponse(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        created_at=msg.created_at,
    )
