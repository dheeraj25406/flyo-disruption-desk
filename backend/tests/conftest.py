import pytest


@pytest.fixture(autouse=True)
def disable_external_gemini_for_unit_tests(monkeypatch):
    """Keep normal unit tests offline even on a developer machine with a real key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield
