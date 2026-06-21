"""SQLite-backed persistence for :mod:`tutor.services.conversations`.

Schema:

  conversations(session_id PK, user_id, title, message_count,
                last_message_preview, created_at, updated_at)
  messages(id PK, session_id FK, role, content, job_id, capability,
           created_at, metadata)

Both tables are created on first ``init()``. Writes go through a
single asyncio lock to keep the message_count column and the
messages table consistent under concurrent appends.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from tutor.services.config.settings import get_settings

from .schema import Conversation, ConversationDetail, Message

_Base = declarative_base()


class ConversationRow(_Base):  # type: ignore[misc]
    __tablename__ = "conversations"

    session_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="")
    message_count = Column(Integer, nullable=False, default=0)
    last_message_preview = Column(String(280), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class MessageRow(_Base):  # type: ignore[misc]
    __tablename__ = "messages"

    id = Column(String(64), primary_key=True)
    session_id = Column(
        String(64),
        # NOTE: no FK constraint across two engines; we keep the
        # cascade delete in the service layer so we don't depend on
        # SQLite's PRAGMA foreign_keys at runtime.
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False, default="")
    job_id = Column(String(64), nullable=True)
    capability = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    msg_metadata = Column(JSON, nullable=False, default=dict)


class ConversationStore:
    """Persistence for conversations and messages."""

    def __init__(self, *, db_path: str | None = None) -> None:
        settings = get_settings()
        self._db_path = (
            db_path
            or str(Path(settings.data_dir) / "conversations.db")
        )
        self._engine: AsyncEngine | None = None
        self._sessionmaker: sessionmaker | None = None  # type: ignore[type-arg]
        self._write_lock: asyncio.Lock | None = None
        # Tests can pre-create the engine via _init_engine.
        self._init_lock = threading.Lock()

    # ---- lifecycle ------------------------------------------------------

    def _ensure_engine(self) -> AsyncEngine:
        if self._engine is None:
            with self._init_lock:
                if self._engine is None:
                    Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
                    url = f"sqlite+aiosqlite:///{self._db_path}"
                    self._engine = create_async_engine(
                        url, future=True, echo=False
                    )
                    self._sessionmaker = sessionmaker(
                        self._engine, expire_on_commit=False, class_=AsyncSession
                    )
        return self._engine

    async def init(self) -> None:
        engine = self._ensure_engine()
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    def _session(self) -> AsyncSession:
        if self._sessionmaker is None:
            self._ensure_engine()
        assert self._sessionmaker is not None
        return self._sessionmaker()

    # ---- conversations --------------------------------------------------

    async def get_or_create(
        self,
        *,
        session_id: str,
        user_id: str,
        title: str | None = None,
    ) -> Conversation:
        await self.init()
        async with self._session() as session:
            row = await session.get(ConversationRow, session_id)
            if row is not None:
                if row.user_id != user_id:
                    raise PermissionError(
                        f"session {session_id} belongs to a different user"
                    )
                return _row_to_conversation(row)
            now = datetime.now(timezone.utc)
            row = ConversationRow(
                session_id=session_id,
                user_id=user_id,
                title=title or "",
                message_count=0,
                last_message_preview="",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            return _row_to_conversation(row)

    async def get(self, session_id: str) -> Conversation | None:
        await self.init()
        async with self._session() as session:
            row = await session.get(ConversationRow, session_id)
            if row is None:
                return None
            return _row_to_conversation(row)

    async def list_for_user(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        await self.init()
        async with self._session() as session:
            stmt = (
                select(ConversationRow)
                .where(ConversationRow.user_id == user_id)
                .order_by(ConversationRow.updated_at.desc())
                .limit(limit + 1)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            count_stmt = select(ConversationRow).where(
                ConversationRow.user_id == user_id
            )
            total = len((await session.execute(count_stmt)).scalars().all())
            return [_row_to_conversation(r) for r in rows], total

    async def update(
        self, session_id: str, *, title: str | None = None
    ) -> Conversation | None:
        await self.init()
        async with self._session() as session:
            row = await session.get(ConversationRow, session_id)
            if row is None:
                return None
            if title is not None:
                row.title = title
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _row_to_conversation(row)

    async def delete(self, session_id: str) -> bool:
        await self.init()
        assert self._write_lock is not None
        async with self._write_lock:
            async with self._session() as session:
                row = await session.get(ConversationRow, session_id)
                if row is None:
                    return False
                # Cascade messages manually.
                msgs = (
                    await session.execute(
                        select(MessageRow).where(
                            MessageRow.session_id == session_id
                        )
                    )
                ).scalars().all()
                for m in msgs:
                    await session.delete(m)
                await session.delete(row)
                await session.commit()
                return True

    # ---- messages -------------------------------------------------------

    async def append_message(
        self, session_id: str, msg: Message
    ) -> Message | None:
        """Append a message, updating the conversation summary in
        one transaction. Returns the persisted message, or None if
        the conversation does not exist."""
        await self.init()
        assert self._write_lock is not None
        async with self._write_lock:
            async with self._session() as session:
                conv = await session.get(ConversationRow, session_id)
                if conv is None:
                    return None
                if not msg.id:
                    msg = msg.model_copy(update={"id": uuid.uuid4().hex})
                if msg.created_at is None:
                    msg = msg.model_copy(
                        update={"created_at": datetime.now(timezone.utc)}
                    )
                m = MessageRow(
                    id=msg.id,
                    session_id=session_id,
                    role=msg.role,
                    content=msg.content,
                    job_id=msg.job_id,
                    capability=msg.capability,
                    created_at=msg.created_at,
                    msg_metadata=msg.metadata,
                )
                session.add(m)
                conv.message_count = (conv.message_count or 0) + 1
                conv.last_message_preview = (msg.content or "")[:280]
                # Auto-title from the first user message.
                if (
                    not conv.title
                    and msg.role == "user"
                    and msg.content.strip()
                ):
                    conv.title = msg.content.strip()[:60]
                conv.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return _to_message(m)

    async def list_messages(
        self, session_id: str
    ) -> list[Message]:
        await self.init()
        async with self._session() as session:
            rows = (
                await session.execute(
                    select(MessageRow)
                    .where(MessageRow.session_id == session_id)
                    .order_by(MessageRow.created_at.asc())
                )
            ).scalars().all()
            return [_to_message(r) for r in rows]

    async def get_conversation_with_messages(
        self, session_id: str
    ) -> ConversationDetail | None:
        await self.init()
        async with self._session() as session:
            row = await session.get(ConversationRow, session_id)
            if row is None:
                return None
            msgs = (
                await session.execute(
                    select(MessageRow)
                    .where(MessageRow.session_id == session_id)
                    .order_by(MessageRow.created_at.asc())
                )
            ).scalars().all()
            return ConversationDetail(
                **_row_to_conversation(row).model_dump(),
                messages=[_to_message(m) for m in msgs],
            )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_store: ConversationStore | None = None
_lock = threading.Lock()


def get_conversation_store() -> ConversationStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = ConversationStore()
    return _store


def reset_conversation_store() -> None:
    global _store
    _store = None


# ---------------------------------------------------------------------------
# Row → model helpers
# ---------------------------------------------------------------------------


def _row_to_conversation(row: ConversationRow) -> Conversation:
    return Conversation(
        session_id=row.session_id,
        user_id=row.user_id,
        title=row.title or "",
        message_count=row.message_count or 0,
        last_message_preview=row.last_message_preview or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_message(row: MessageRow) -> Message:
    return Message(
        id=row.id,
        role=row.role,  # type: ignore[arg-type]
        content=row.content or "",
        job_id=row.job_id,
        capability=row.capability,
        created_at=row.created_at,
        metadata=dict(row.msg_metadata or {}),
    )
