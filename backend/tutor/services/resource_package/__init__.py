"""Resource package service.

Models + persistence for the multi-modal learning resources generated
by Tutor:

- :class:`Resource`       — a single resource (one of 7 types)
- :class:`ResourcePackage` — a bundle of resources for one topic + learner
- :class:`ResourcePackageStore` — SQLite-backed persistence (Phase 5+)

Design follows idea.md closely. All Resource fields are JSON-serialisable so
the package can be persisted (Phase 5) and streamed to the frontend.
"""

from tutor.services.resource_package.schema import (
    ArtifactRef,
    CodeResource,
    DocumentResource,
    ExerciseOption,
    ExerciseQuestion,
    ExerciseResource,
    MindMapResource,
    PPTResource,
    ReadingResource,
    Resource,
    ResourcePackage,
    ResourceReview,
    ResourceType,
    ReviewVerdict,
    VideoResource,
    build_resource,
)
from tutor.services.resource_package.store import (
    ResourcePackageStore,
    get_resource_package_store,
    reset_resource_package_store,
)

__all__ = [
    "ArtifactRef",
    "CodeResource",
    "DocumentResource",
    "ExerciseOption",
    "ExerciseQuestion",
    "ExerciseResource",
    "MindMapResource",
    "PPTResource",
    "ReadingResource",
    "Resource",
    "ResourcePackage",
    "ResourcePackageStore",
    "ResourceReview",
    "ResourceType",
    "ReviewVerdict",
    "VideoResource",
    "build_resource",
    "get_resource_package_store",
    "reset_resource_package_store",
]
