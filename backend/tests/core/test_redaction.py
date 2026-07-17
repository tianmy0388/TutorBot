"""Unit-level guarantees for the public recursive redactor."""

from __future__ import annotations

import json

from tutor.core.redaction import failure_category, redact_sensitive

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkFkYSJ9."
    "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)
PEM = "-----BEGIN PRIVATE KEY-----\nQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\n-----END PRIVATE KEY-----"
OPAQUE = "Z8q1Wm4Nv7Rx2Kp9Bd6Hy3Lc5Tg0Fs8Ua1Je4Ci7"
OPAQUE_HEX = "9f4c2a7d8e1b6c3f0a5d9e2b7c4f1a8d6e3b0c5f9a2d7e4b1c8f6a3d0e5b9c2f"


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


def test_structured_high_certainty_credentials_are_always_redacted() -> None:
    public = redact_sensitive(
        {
            "refresh_token": "short-refresh-value",
            "session_token": "short-session-value",
            "auth_token": JWT,
            "private_key": PEM,
            "client_secret": "tiny-secret",
            "access_key": "short-access-key",
            "secret_key": "short-secret-key",
            "authorization": "Basic dXNlcjpwYXNz",
            "cookie": "sessionid=short-cookie",
        }
    )
    encoded = json.dumps(public)
    for secret in (
        "short-refresh-value",
        "short-session-value",
        JWT,
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo",
        "tiny-secret",
        "short-access-key",
        "short-secret-key",
        "dXNlcjpwYXNz",
        "short-cookie",
    ):
        assert secret not in encoded


def test_ambiguous_token_fields_preserve_educational_identifiers() -> None:
    public = redact_sensitive(
        {
            "token": "identifier",
            "lesson": {"token": "lexical_unit", "next_token": "operator"},
        }
    )
    assert public == {
        "token": "identifier",
        "lesson": {"token": "lexical_unit", "next_token": "operator"},
    }

    credential = redact_sensitive({"token": JWT})
    assert credential == {"token": "[REDACTED]"}
    opaque = redact_sensitive({"token": OPAQUE})
    assert opaque == {"token": "[REDACTED]"}
    opaque_hex = redact_sensitive({"token": OPAQUE_HEX})
    assert opaque_hex == {"token": "[REDACTED]"}


def test_generated_code_remains_usable_while_embedded_credentials_are_removed() -> None:
    source = (
        "token = tokenizer.next_token()\n"
        "api_key = \"sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\"\n"
        f"session = \"{JWT}\"\n"
        "print(token)\n"
    )
    public = redact_sensitive({"source_code": source, "content": source})
    for field in ("source_code", "content"):
        scrubbed = public[field]
        assert isinstance(scrubbed, str)
        assert "token = tokenizer.next_token()" in scrubbed
        assert "print(token)" in scrubbed
        assert "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in scrubbed
        assert JWT not in scrubbed
        compile(scrubbed, f"<{field}>", "exec")


def test_pem_jwt_and_auth_values_are_redacted_inside_free_text() -> None:
    public = redact_sensitive(
        {
            "notes": f"Authorization: Basic dXNlcjpwYXNz JWT={JWT}\n{PEM}",
        }
    )
    encoded = json.dumps(public)
    assert "dXNlcjpwYXNz" not in encoded
    assert JWT not in encoded
    assert "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo" not in encoded
