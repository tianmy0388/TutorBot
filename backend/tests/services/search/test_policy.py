from tutor.services.search import SearchPolicy


def test_search_policy_requires_conversation_and_runtime_flags() -> None:
    assert SearchPolicy.allowed(conversation_enabled=True, runtime_enabled=True) is True
    assert SearchPolicy.allowed(conversation_enabled=True, runtime_enabled=False) is False
    assert SearchPolicy.allowed(conversation_enabled=False, runtime_enabled=True) is False
    assert SearchPolicy.allowed(conversation_enabled=False, runtime_enabled=False) is False
