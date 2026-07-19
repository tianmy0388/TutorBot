"""Canonical user identity policy."""

from tutor.services.identity.policy import (
    LOCAL_USER_ID,
    IdentityPolicy,
    IdentityRequired,
    identity_policy_for,
)

__all__ = [
    "LOCAL_USER_ID",
    "IdentityPolicy",
    "IdentityRequired",
    "identity_policy_for",
]
