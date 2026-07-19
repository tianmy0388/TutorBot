"""Resolve requested identities consistently at transport boundaries."""

from __future__ import annotations

from starlette.requests import HTTPConnection

from tutor.services.config.settings import get_settings

LOCAL_USER_ID = "local-user"


class IdentityRequired(ValueError):  # noqa: N818 - public interface name
    """Raised when multi-user mode receives no explicit identity."""


class IdentityPolicy:
    def __init__(self, multi_user_enabled: bool) -> None:
        self.multi_user_enabled = multi_user_enabled

    def resolve(self, requested_user_id: str | None) -> str:
        if not self.multi_user_enabled:
            return LOCAL_USER_ID
        if not requested_user_id:
            raise IdentityRequired("user_id is required when multi-user mode is enabled")
        return requested_user_id


def identity_policy_for(connection: HTTPConnection) -> IdentityPolicy:
    """Build the policy configured for the connection's application."""
    settings = getattr(connection.app.state, "settings", None) or get_settings()
    return IdentityPolicy(multi_user_enabled=settings.multi_user_enabled)


__all__ = [
    "LOCAL_USER_ID",
    "IdentityPolicy",
    "IdentityRequired",
    "identity_policy_for",
]
