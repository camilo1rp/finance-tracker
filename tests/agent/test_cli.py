from app.agent.cli import _parse_args


def test_cli_defaults_to_coordinator() -> None:
    args = _parse_args(["thread-1"])
    assert args.thread_id == "thread-1"
    assert args.steward is False


def test_cli_steward_flag() -> None:
    args = _parse_args(["--steward", "thread-2"])
    assert args.thread_id == "thread-2"
    assert args.steward is True
