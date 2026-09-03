from app.agent.cli import _decision, _parse_args


def test_cli_defaults_to_coordinator() -> None:
    args = _parse_args(["thread-1"])
    assert args.thread_id == "thread-1"
    assert args.steward is False


def test_cli_steward_flag() -> None:
    args = _parse_args(["--steward", "thread-2"])
    assert args.thread_id == "thread-2"
    assert args.steward is True


def test_cli_edit_indexes_ops(monkeypatch) -> None:
    ops = [
        {"op": "create", "raw_value": "a"},
        {"op": "create", "raw_value": "b"},
        {"op": "update", "mapping_id": 8},
    ]
    monkeypatch.setattr("builtins.input", lambda _: "edit 0,2")
    decision = _decision(ops)
    assert decision == {"decision": "approve", "ops": [ops[0], ops[2]]}
