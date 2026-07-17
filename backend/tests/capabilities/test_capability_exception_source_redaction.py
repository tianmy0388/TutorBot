"""Static guard against reintroducing caught-exception disclosure."""

from __future__ import annotations

import inspect

import pytest
from tutor.capabilities import assessment, path_planning, profile, resource_generation, tutoring


@pytest.mark.parametrize(
    "module",
    [assessment, path_planning, profile, resource_generation, tutoring],
)
def test_capability_source_never_formats_caught_exception_details(module) -> None:
    source = inspect.getsource(module)
    assert "{exc}" not in source
    assert "{exc!r}" not in source
    assert "logger.exception" not in source
    assert "traceback.format" not in source
