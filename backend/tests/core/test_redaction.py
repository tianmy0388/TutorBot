"""Unit-level guarantees for the public recursive redactor."""

from __future__ import annotations

import json

from tutor.core.redaction import failure_category, redact_sensitive


def test_recursive_redaction_is_bounded_and_preserves_educational_text() -> None:
    value: dict[str, object] = {
        "lesson": "Tokenization maps a normal token to an integer.",
        "api_key": "SECRET_TOKEN_STRUCTURED_123",
    }
    cursor = value
    for index in range(20):
        nested: dict[str, object] = {
            "note": f"level {index}",
            "password": "deep-password",
        }
        cursor["nested"] = nested
        cursor = nested

    public = redact_sensitive(value, max_depth=5)
    encoded = json.dumps(public, ensure_ascii=False)
    assert "SECRET_TOKEN_STRUCTURED_123" not in encoded
    assert "deep-password" not in encoded
    assert "[TRUNCATED]" in encoded
    assert "Tokenization maps a normal token to an integer." in encoded


def test_credential_patterns_are_redacted_without_matching_word_token() -> None:
    value = {
        "plain": "A token is a lexical unit; tokenization is educational text.",
        "provider": (
            "Bearer bearer-value and https://user:pass@example.invalid/v1 "
            "with token=SECRET_TOKEN_INLINE_123"
        ),
    }
    public = redact_sensitive(value)
    encoded = json.dumps(public)
    assert "bearer-value" not in encoded
    assert "user:pass" not in encoded
    assert "SECRET_TOKEN_INLINE_123" not in encoded
    assert "A token is a lexical unit" in encoded


def test_failure_category_uses_exception_type_not_message() -> None:
    class ProviderTimeoutError(RuntimeError):
        pass

    assert failure_category(ProviderTimeoutError("provider body is private")) == "timeout"
    assert failure_category(RuntimeError("mentions timeout only in message")) == "operation"
