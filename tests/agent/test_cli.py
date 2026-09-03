from contextlib import contextmanager

from langchain_core.messages import AIMessage

from app.agent.cli import _config, _decision, _parse_args, main


def test_cli_defaults_to_coordinator() -> None:
    args = _parse_args(["thread-1"])
    assert args.thread_id == "thread-1"
    assert args.steward is False


def test_cli_steward_flag() -> None:
    args = _parse_args(["--steward", "thread-2"])
    assert args.thread_id == "thread-2"
    assert args.steward is True


def test_cli_config_carries_trace_metadata() -> None:
    coordinator = _config("thread-coord")
    assert coordinator["run_name"] == "coordinator-turn"
    assert coordinator["tags"] == ["cli"]
    assert coordinator["metadata"] == {"thread_id": "thread-coord", "entrypoint": "cli"}
    assert coordinator["configurable"]["thread_id"] == "thread-coord"

    steward = _config("thread-stew", steward=True)
    assert steward["run_name"] == "steward-turn"
    assert steward["tags"] == ["steward-cli"]
    assert steward["metadata"] == {
        "thread_id": "thread-stew",
        "entrypoint": "steward-cli",
    }


def test_cli_invoke_and_resume_pass_trace_config(monkeypatch) -> None:
    captured: list[dict] = []

    class FakeGraph:
        def get_state(self, config: dict):
            captured.append(config)

            class Snapshot:
                interrupts = ()

            return Snapshot()

        def invoke(self, _payload, config: dict):
            captured.append(config)
            return {"messages": [AIMessage(content="done")]}

    @contextmanager
    def fake_open_checkpointer(**_kwargs):
        yield object()

    inputs = iter(["hello", "quit"])

    monkeypatch.setattr("app.agent.cli.open_checkpointer", fake_open_checkpointer)
    monkeypatch.setattr(
        "app.agent.cli.build_coordinator",
        lambda **_kwargs: FakeGraph(),
    )
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    main(["thread-invoke"])

    assert len(captured) >= 2
    for config in captured:
        assert config["run_name"] == "coordinator-turn"
        assert config["tags"] == ["cli"]
        assert config["metadata"]["thread_id"] == "thread-invoke"
        assert config["metadata"]["entrypoint"] == "cli"


def test_cli_edit_indexes_ops(monkeypatch) -> None:
    ops = [
        {"op": "create", "raw_value": "a"},
        {"op": "create", "raw_value": "b"},
        {"op": "update", "mapping_id": 8},
    ]
    monkeypatch.setattr("builtins.input", lambda _: "edit 0,2")
    decision = _decision(ops)
    assert decision == {"decision": "approve", "ops": [ops[0], ops[2]]}
