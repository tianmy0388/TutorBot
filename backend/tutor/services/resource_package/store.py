"""ResourcePackageStore — SQLite-backed persistence for generated resources.

A :class:`ResourcePackage` is a bundle of :class:`Resource` objects
generated for one learner on one topic. We persist both levels so that:

- The frontend can load history across sessions
- The adaptive strategy engine can see *what* was previously pushed
- Assessment can correlate events with resources
- A future async-job layer can write completed jobs here as they finish

Schema (two tables — package header + per-resource rows):

    resource_packages
        package_id  (PK, uuid hex)
        user_id     (indexed)
        topic
        resource_count, total_minutes, avg_confidence
        generated_by           (JSON list)
        target_profile_snapshot (JSON dict)
        learning_path_summary  (JSON dict)
        package_metadata       (JSON dict)
        created_at             (indexed)

    resources
        resource_id  (PK, uuid hex)
        package_id   (FK → resource_packages.package_id, indexed)
        user_id      (denormalized, indexed)
        type, title, content
        format_specific (JSON)
        difficulty, estimated_minutes, confidence_score
        prerequisites, generated_by, tags, topic
        resource_metadata (JSON)
        created_at

Design mirrors :class:`LearningEventStore` (SQLite + SQLAlchemy 2.0
async, singleton with thread lock).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from loguru import logger
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.artifacts import (
    UnsafeArtifactKey,
    resolve_artifact_key,
    to_artifact_key,
)
from tutor.services.config.settings import get_settings
from tutor.services.resource_package.schema import Resource, ResourcePackage


class _Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM rows
# ---------------------------------------------------------------------------


class PackageRow(_Base):
    """One :class:`ResourcePackage` header."""

    __tablename__ = "resource_packages"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    package_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(128), nullable=False, index=True)
    topic = Column(String(512), nullable=False, default="")
    resource_count = Column(Integer, nullable=False, default=0)
    total_minutes = Column(Integer, nullable=False, default=0)
    avg_confidence = Column(Float, nullable=False, default=0.0)
    generated_by = Column(JSON, nullable=False, default=list)
    target_profile_snapshot = Column(JSON, nullable=False, default=dict)
    learning_path_summary = Column(JSON, nullable=False, default=dict)
    package_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)

    resources = relationship(
        "ResourceRow",
        back_populates="package",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_packages_user_time", "user_id", "created_at"),
    )


class ResourceRow(_Base):
    """One :class:`Resource` row, child of a :class:`PackageRow`."""

    __tablename__ = "resources"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    resource_id = Column(String(64), nullable=False, unique=True)
    package_id = Column(
        String(64),
        ForeignKey("resource_packages.package_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String(128), nullable=False, index=True)
    type = Column(String(32), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    content = Column(String, nullable=False, default="")
    format_specific = Column(JSON, nullable=False, default=dict)
    difficulty = Column(Integer, nullable=False, default=2)
    estimated_minutes = Column(Integer, nullable=False, default=5)
    prerequisites = Column(JSON, nullable=False, default=list)
    generated_by = Column(JSON, nullable=False, default=list)
    confidence_score = Column(Float, nullable=False, default=0.7)
    topic = Column(String(512), nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    resource_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)

    package = relationship("PackageRow", back_populates="resources")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ResourcePackageStore:
    """Async SQLite store for :class:`ResourcePackage` and :class:`Resource`."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = get_settings().data_dir / "resource_packages.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._write_lock: asyncio.Lock | None = None
        self._lock = threading.Lock()

    # ---- lifecycle --------------------------------------------------------

    async def init(self) -> None:
        engine = self._ensure_engine()
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        logger.info(f"ResourcePackageStore ready at {self.db_path}")

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            self._write_lock = None

    def _ensure_engine(self) -> AsyncEngine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    url = f"sqlite+aiosqlite:///{self.db_path}"
                    self._engine = create_async_engine(
                        url,
                        echo=False,
                        future=True,
                        connect_args={"check_same_thread": False},
                    )
                    self._sessionmaker = async_sessionmaker(
                        self._engine, expire_on_commit=False
                    )
                    self._write_lock = asyncio.Lock()
        return self._engine

    def _with_session(self):
        """Context manager: yield an AsyncSession inside a transaction."""
        if self._sessionmaker is None:
            self._ensure_engine()
        assert self._sessionmaker is not None

        store = self

        class _Ctx:
            async def __aenter__(self):
                self._s = store._sessionmaker()  # type: ignore[union-attr]
                return self._s

            async def __aexit__(self, exc_type, exc, tb):
                try:
                    if exc_type is None:
                        await self._s.commit()
                    else:
                        await self._s.rollback()
                finally:
                    await self._s.close()

        return _Ctx()

    # ---- writes -----------------------------------------------------------

    async def save(self, package: ResourcePackage, user_id: str | None = None) -> ResourcePackage:
        """Persist a :class:`ResourcePackage` (insert-or-replace semantics).

        If a package with the same ``package_id`` already exists, it's
        fully replaced (resources cascade-deleted and re-inserted).
        """
        self._ensure_engine()
        assert self._write_lock is not None

        # Normalise: fill summary fields so list views don't have to load
        # child rows.
        uid = user_id or (package.metadata.get("user_id") or "anonymous")
        # Persist user_id in metadata for cross-system queries (read-only
        # round-trip — we don't expose it on the wire).
        package.metadata.setdefault("user_id", uid)
        summary = package.summary()

        async with self._write_lock, self._with_session() as session:
            # Wipe any existing package with the same id (cascade kills
            # children).
            existing = await session.execute(
                select(PackageRow).where(
                    PackageRow.package_id == package.package_id
                )
            )
            existing_row = existing.scalar_one_or_none()
            if existing_row is not None:
                await session.delete(existing_row)
                await session.flush()

            pkg_row = PackageRow(
                package_id=package.package_id,
                user_id=uid,
                topic=package.topic or "",
                resource_count=summary["resource_count"],
                total_minutes=summary["total_minutes"],
                avg_confidence=summary["avg_confidence"],
                generated_by=list(package.generated_by or []),
                target_profile_snapshot=dict(package.target_profile_snapshot or {}),
                learning_path_summary=dict(package.learning_path_summary or {}),
                package_metadata=dict(package.metadata or {}),
                created_at=package.created_at,
            )
            session.add(pkg_row)

            for r in package.resources:
                format_specific = _portable_format_specific(
                    r.format_specific, get_settings().data_dir
                )
                session.add(
                    ResourceRow(
                        resource_id=r.resource_id,
                        package_id=package.package_id,
                        user_id=uid,
                        type=r.type.value,
                        title=r.title,
                        content=r.content or "",
                        format_specific=format_specific,
                        difficulty=r.difficulty,
                        estimated_minutes=r.estimated_minutes,
                        prerequisites=list(r.prerequisites or []),
                        generated_by=list(r.generated_by or []),
                        confidence_score=float(r.confidence_score),
                        topic=r.topic or "",
                        tags=list(r.tags or []),
                        resource_metadata=dict(r.metadata or {}),
                        created_at=r.created_at,
                    )
                )

        logger.info(
            f"ResourcePackageStore.save pkg={package.package_id[:12]}… "
            f"user={uid} resources={len(package.resources)}"
        )
        return package

    async def save_many(
        self, packages: Iterable[ResourcePackage], user_id: str | None = None
    ) -> int:
        """Persist many packages; returns the count saved."""
        count = 0
        for pkg in packages:
            await self.save(pkg, user_id=user_id)
            count += 1
        return count

    async def update_resource(
        self,
        package_id: str,
        resource: Resource,
    ) -> Resource:
        """Atomically replace one resource row without rewriting siblings."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.package_id == package_id,
                        ResourceRow.resource_id == resource.resource_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(
                    f"resource not found: {package_id}/{resource.resource_id}"
                )
            row.type = resource.type.value
            row.title = resource.title
            row.content = resource.content or ""
            row.format_specific = _portable_format_specific(
                resource.format_specific,
                get_settings().data_dir,
            )
            row.difficulty = resource.difficulty
            row.estimated_minutes = resource.estimated_minutes
            row.prerequisites = list(resource.prerequisites or [])
            row.generated_by = list(resource.generated_by or [])
            row.confidence_score = float(resource.confidence_score)
            row.topic = resource.topic or ""
            row.tags = list(resource.tags or [])
            row.resource_metadata = dict(resource.metadata or {})
            row.created_at = resource.created_at
        return resource

    async def delete(self, package_id: str) -> bool:
        """Delete a package by id. Returns True if a row was removed."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            row = (
                await session.execute(
                    select(PackageRow).where(
                        PackageRow.package_id == package_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
        return True

    async def delete_user(self, user_id: str) -> int:
        """Delete all packages for a user. Returns count removed."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            rows = (
                await session.execute(
                    select(PackageRow).where(PackageRow.user_id == user_id)
                )
            ).scalars().all()
            count = len(rows)
            for r in rows:
                await session.delete(r)
        return count

    # ---- reads ------------------------------------------------------------

    async def get(self, package_id: str) -> ResourcePackage | None:
        """Load a full :class:`ResourcePackage` (header + resources)."""
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(PackageRow)
                    .where(PackageRow.package_id == package_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_package(row)

    async def get_resource(self, resource_id: str) -> Resource | None:
        """Load a single :class:`Resource` by its id."""
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.resource_id == resource_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_resource(row)

    async def list(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
        until: datetime | None = None,
        topic: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List package summaries (header only, no resources) for a user.

        Returns a list of dicts (ResourcePackage.summary() shape + user_id)
        ordered by created_at DESC. Each item includes a ``types`` list
        of the resource-type values present in the package; this is
        fetched with a single batched query so the list call stays
        O(1) round-trips regardless of result size.

        If ``session_id`` is provided, the store filters by the
        ``session_id`` field stored in ``package_metadata`` (set by
        the runner when the package is saved). This is what powers the
        conversation-detail aggregation endpoint (2026-06-21 plan):
        the front-end asks "which packages belong to this conversation
        id?" and we answer in a single SQL round-trip.
        """
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(PackageRow).where(PackageRow.user_id == user_id)
            if since is not None:
                stmt = stmt.where(PackageRow.created_at >= since)
            if until is not None:
                stmt = stmt.where(PackageRow.created_at <= until)
            if topic:
                stmt = stmt.where(PackageRow.topic.like(f"%{topic}%"))
            if session_id is not None:
                # ``package_metadata`` is a JSON column; on SQLite the
                # ``json_extract`` accessor is exposed via
                # ``JSON.col["key"].as_string()``.
                stmt = stmt.where(
                    PackageRow.package_metadata["session_id"].as_string()
                    == session_id
                )
            stmt = stmt.order_by(PackageRow.created_at.desc()).limit(limit).offset(offset)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                return []
            # Batched type lookup: one IN query for all rows.
            ids = [r.package_id for r in rows]
            type_rows = (
                await session.execute(
                    select(ResourceRow.package_id, ResourceRow.type).where(
                        ResourceRow.package_id.in_(ids)
                    )
                )
            ).all()
            types_by_pkg: dict[str, set[str]] = {pid: set() for pid in ids}
            for pid, t in type_rows:
                if pid in types_by_pkg:
                    types_by_pkg[pid].add(t)
            return [
                self._row_to_summary(r, sorted(types_by_pkg[r.package_id]))
                for r in rows
            ]

    async def list_for_session(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> list[ResourcePackage]:
        """Load full packages for an already-authorized conversation."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = (
                select(PackageRow)
                .where(
                    PackageRow.package_metadata["session_id"].as_string()
                    == session_id
                )
                .order_by(PackageRow.created_at.desc(), PackageRow.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_package(row) for row in reversed(rows)]

    async def count(self, user_id: str) -> int:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(PackageRow).where(PackageRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            return len(rows)

    async def stats(self, user_id: str) -> dict[str, Any]:
        """Aggregate stats for a user."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(PackageRow).where(PackageRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            return {
                "package_count": 0,
                "resource_count": 0,
                "total_minutes": 0,
                "avg_confidence": 0.0,
                "topics": [],
                "type_counts": {},
                "first_at": None,
                "last_at": None,
            }

        type_counts: dict[str, int] = {}
        topics: set[str] = set()
        total_minutes = 0
        total_conf = 0.0
        total_resources = 0

        for r in rows:
            topics.add(r.topic or "(no topic)")
            total_minutes += r.total_minutes
            total_conf += r.avg_confidence * r.resource_count
            total_resources += r.resource_count
            # Re-query for type breakdown (cheap on small N)
            async with self._with_session() as session2:
                res_rows = (
                    await session2.execute(
                        select(ResourceRow.type).where(
                            ResourceRow.package_id == r.package_id
                        )
                    )
                ).all()
                for (t,) in res_rows:
                    type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "package_count": len(rows),
            "resource_count": total_resources,
            "total_minutes": total_minutes,
            "avg_confidence": (
                round(total_conf / total_resources, 3) if total_resources else 0.0
            ),
            "topics": sorted(topics),
            "type_counts": type_counts,
            "first_at": min(r.created_at for r in rows).isoformat(),
            "last_at": max(r.created_at for r in rows).isoformat(),
        }

    # ---- row → model -----------------------------------------------------

    @staticmethod
    def _row_to_resource(row: ResourceRow) -> Resource:
        return Resource(
            resource_id=row.resource_id,
            type=row.type,  # ResourceType enum is str-typed
            title=row.title,
            content=row.content or "",
            format_specific=dict(row.format_specific or {}),
            difficulty=row.difficulty,
            estimated_minutes=row.estimated_minutes,
            prerequisites=list(row.prerequisites or []),
            generated_by=list(row.generated_by or []),
            confidence_score=float(row.confidence_score),
            topic=row.topic or "",
            tags=list(row.tags or []),
            created_at=row.created_at,
            metadata=dict(row.resource_metadata or {}),
        )

    @classmethod
    def _row_to_package(cls, row: PackageRow) -> ResourcePackage:
        resources = [cls._row_to_resource(r) for r in row.resources]
        # Read user_id from metadata (denormalized); fall back to the row
        user_id = (row.package_metadata or {}).get("user_id", row.user_id)
        return ResourcePackage(
            package_id=row.package_id,
            topic=row.topic or "",
            resources=resources,
            target_profile_snapshot=dict(row.target_profile_snapshot or {}),
            learning_path_summary=dict(row.learning_path_summary or {}),
            created_at=row.created_at,
            generated_by=list(row.generated_by or []),
            metadata={**(row.package_metadata or {}), "user_id": user_id},
        )

    @staticmethod
    def _row_to_summary(
        row: PackageRow,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Materialise a package row to the wire summary shape.

        The frontend (``/resources`` page) renders a per-package chip
        strip of resource types, so ``types`` must be present even when
        only the header row is loaded. Callers that have not pre-fetched
        the type set pass ``None`` and we default to an empty list; the
        list endpoint (see :meth:`list`) supplies a real value via a
        single batched query to avoid an N+1.
        """
        return {
            "package_id": row.package_id,
            "user_id": row.user_id,
            "topic": row.topic,
            "resource_count": row.resource_count,
            "total_minutes": row.total_minutes,
            "avg_confidence": row.avg_confidence,
            "generated_by": list(row.generated_by or []),
            "types": list(types or []),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_store: ResourcePackageStore | None = None
_store_lock = threading.Lock()


def get_resource_package_store() -> ResourcePackageStore:
    """Return the process-wide :class:`ResourcePackageStore` (cached)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ResourcePackageStore()
                logger.info("ResourcePackageStore singleton created")
    return _store


def reset_resource_package_store() -> None:
    """Clear the cached singleton. Used by tests."""
    global _store
    _store = None


def _portable_format_specific(
    value: dict[str, Any] | None,
    data_dir: Path,
) -> dict[str, Any]:
    """Normalize known local-file fields before a new database write."""
    payload = dict(value or {})
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        payload["artifacts"] = [
            normalized
            for entry in artifacts
            if isinstance(entry, dict)
            for normalized in [_portable_artifact_entry(entry, data_dir)]
            if normalized is not None
        ]

    for legacy_name in ("path", "mp4_path", "pptx_path"):
        legacy_value = payload.pop(legacy_name, None)
        if legacy_value and not payload.get("artifact_key"):
            key = _path_value_to_key(str(legacy_value), data_dir)
            if key is not None:
                payload["artifact_key"] = key
            else:
                payload["artifact_unresolved"] = True

    legacy_url = payload.get("url")
    if legacy_url and not payload.get("artifact_key"):
        parsed = urlsplit(str(legacy_url))
        if parsed.scheme not in {"http", "https"}:
            raw_url = unquote(parsed.path) if parsed.scheme == "file" else str(legacy_url)
            if raw_url.startswith("/static/manim/"):
                raw_url = f"manim_videos/{raw_url.removeprefix('/static/manim/')}"
            key = _path_value_to_key(raw_url, data_dir)
            payload.pop("url", None)
            if key is not None:
                payload["artifact_key"] = key
            else:
                payload["artifact_unresolved"] = True

    key = payload.get("artifact_key")
    if key:
        try:
            resolve_artifact_key(str(key), data_dir)
        except UnsafeArtifactKey:
            payload.pop("artifact_key", None)
            payload["artifact_unresolved"] = True
    return payload


def _portable_artifact_entry(
    entry: dict[str, Any],
    data_dir: Path,
) -> dict[str, Any] | None:
    result = {
        key: entry[key]
        for key in ("name", "kind")
        if key in entry and entry[key] is not None
    }
    raw = entry.get("artifact_key") or entry.get("path") or entry.get("url")
    if not raw:
        return result or None
    parsed = urlsplit(str(raw))
    if parsed.scheme in {"http", "https"}:
        result["url"] = str(raw)
        return result
    if parsed.scheme == "file":
        raw = unquote(parsed.path)
    elif "url" in entry and str(raw).startswith("/static/manim/"):
        raw = f"manim_videos/{str(raw).removeprefix('/static/manim/')}"
    key = _path_value_to_key(str(raw), data_dir, already_key="artifact_key" in entry)
    if key is None:
        result["unresolved"] = True
    else:
        result["artifact_key"] = key
    return result


def portable_format_specific(
    value: dict[str, Any] | None,
    data_dir: Path,
) -> dict[str, Any]:
    """Return a wire-safe resource payload with canonical artifact keys."""
    return _portable_format_specific(value, data_dir)


def _path_value_to_key(
    value: str,
    data_dir: Path,
    *,
    already_key: bool = False,
) -> str | None:
    if already_key:
        try:
            resolve_artifact_key(value, data_dir)
            return value
        except UnsafeArtifactKey:
            return None
    path = Path(value)
    if path.is_absolute():
        try:
            return to_artifact_key(path, data_dir)
        except UnsafeArtifactKey:
            return None
    try:
        resolve_artifact_key(value, data_dir)
    except UnsafeArtifactKey:
        return None
    return value.replace("\\", "/")


__all__ = [
    "PackageRow",
    "ResourcePackageStore",
    "ResourceRow",
    "portable_format_specific",
    "get_resource_package_store",
    "reset_resource_package_store",
]
