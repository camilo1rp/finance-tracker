import os


def test_tracing_env_vars_are_disabled() -> None:
    assert os.environ.get("LANGSMITH_TRACING") == "false"
    assert os.environ.get("LANGCHAIN_TRACING_V2") == "false"
    assert os.environ.get("LANGCHAIN_TRACING") == "false"


def test_tracing_is_not_enabled() -> None:
    try:
        from langsmith.utils import tracing_is_enabled
    except ImportError:
        return
    assert tracing_is_enabled() is False
