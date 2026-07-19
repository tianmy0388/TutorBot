"""Signal detector gate: cheap heuristic before the LLM extractor."""

from __future__ import annotations

import pytest

from tutor.services.learner_profile.signal_detector import detect_profile_signal


@pytest.mark.parametrize(
    "message",
    [
        "我是CS研一，想学LSTM",
        "我现在是本科生，计算机专业",
        "我的专业是软件工程",
        "I'm a graduate student",
        "my major is computer science",
        "我是博士生，研究方向是NLP",
    ],
)
def test_strong_identity_always_triggers(message: str) -> None:
    assert detect_profile_signal(message, has_profile=True) is True
    assert detect_profile_signal(message, has_profile=False) is True


@pytest.mark.parametrize(
    "message",
    [
        "我想学LSTM，之前学过基础NN但对RNN不太熟",
        "我要学反向传播，以前学过梯度下降",
        "I want to learn transformers, I've studied basic NN",
    ],
)
def test_goal_plus_history_triggers(message: str) -> None:
    assert detect_profile_signal(message, has_profile=True) is True


def test_goal_only_triggers_only_without_profile() -> None:
    assert detect_profile_signal("我想学反向传播", has_profile=False) is True
    assert detect_profile_signal("我想学反向传播", has_profile=True) is False


@pytest.mark.parametrize(
    "message",
    [
        "什么是反向传播？",
        "帮我生成反向传播的讲解",
        "RNN 和 LSTM 有什么区别",
        "",
        "   ",
    ],
)
def test_plain_questions_never_trigger(message: str) -> None:
    assert detect_profile_signal(message, has_profile=False) is False
    assert detect_profile_signal(message, has_profile=True) is False
