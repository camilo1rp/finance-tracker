"""REPL for the coordinator (default) or the standalone steward."""
from __future__ import annotations

import argparse
import uuid

from langgraph.types import Command

from app.agent.config import open_checkpointer
from app.agent.coordinator import build_coordinator
from app.agent.steward_graph import build_steward_graph

RECURSION_LIMIT = 25


def _config(thread_id: str, *, steward: bool = False) -> dict:
    entrypoint = "steward-cli" if steward else "cli"
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
        "run_name": "steward-turn" if steward else "coordinator-turn",
        "tags": [entrypoint],
        "metadata": {"thread_id": thread_id, "entrypoint": entrypoint},
    }


def _op_label(op: dict) -> str:
    kind_of = op.get("op")
    if kind_of == "update":
        return f"update mapping {op.get('mapping_id')} -> {op.get('canonical_value')!r}"
    if kind_of == "delete":
        return f"delete mapping {op.get('mapping_id')}"
    scope = f"account={op.get('account_id')}" if op.get("account_id") else "global"
    merchant = f"merchant={op.get('merchant')}" if op.get("merchant") else "all merchants"
    return (
        f"{op.get('kind')}  {op.get('raw_value')!r} -> "
        f"{op.get('canonical_value')!r}  ({scope}, {merchant})"
    )


def _impact_line(impact: dict) -> str:
    op = impact.get("op") or {}
    kind_of = op.get("op")
    bits = [
        f"would_change={impact.get('would_change')}",
        f"suppressed={impact.get('suppressed_by_override')}",
    ]
    if kind_of == "create":
        bits.append(f"shadowed={impact.get('shadowed_by_existing')}")
        if impact.get("duplicate_of_existing_id") is not None:
            bits.append(f"duplicate_of={impact.get('duplicate_of_existing_id')}")
        if impact.get("conflicts_with_existing_id") is not None:
            bits.append(
                f"CONFLICTS with mapping {impact.get('conflicts_with_existing_id')} "
                f"(existing canonical {impact.get('existing_canonical')!r})"
            )
    elif kind_of == "update":
        bits.append(
            f"{impact.get('old_canonical')!r} -> {impact.get('new_canonical')!r}"
        )
    elif kind_of == "delete":
        bits.append(f"fallback={impact.get('falls_back_to')}")
        bits.append(f"unmapped={impact.get('would_become_unmapped')}")
    return " ".join(bits)


def _print_interrupt(payload: dict) -> None:
    print("\n=== Approval needed (nothing applied yet) ===")
    print(f"Rationale: {payload.get('rationale') or '(none)'}")
    ops = payload.get("ops") or []
    preview = payload.get("preview") or {}
    impacts = {impact.get("index"): impact for impact in preview.get("ops") or []}
    grouped: dict[str, list[int]] = {"update": [], "delete": [], "create": []}
    for i, op in enumerate(ops):
        grouped.setdefault(op.get("op") or "create", []).append(i)
    conflicts = [
        i
        for i, _op in enumerate(ops)
        if (impacts.get(i) or {}).get("conflicts_with_existing_id") is not None
    ]
    if conflicts:
        print("Conflicts:")
        for i in conflicts:
            print(f"  [{i}] {_op_label(ops[i])}  {_impact_line(impacts[i])}")
    for heading, key in (
        ("Updates", "update"),
        ("Deletes", "delete"),
        ("Creates", "create"),
    ):
        conflict_set = set(conflicts)
        indexes = [i for i in (grouped.get(key) or []) if i not in conflict_set]
        if not indexes:
            continue
        print(f"{heading}:")
        for i in indexes:
            extra = f"  {_impact_line(impacts[i])}" if i in impacts else ""
            print(f"  [{i}] {_op_label(ops[i])}{extra}")
    print(
        f"Preview: scanned={preview.get('scanned')}  "
        f"would_change={preview.get('total_would_change')}  "
        f"errors={preview.get('validation_errors')}"
    )
    print("Type: approve | reject | edit 0,2")


def _decision(ops: list) -> dict:
    line = input("decision> ").strip()
    if line.lower().startswith("reject"):
        return {"decision": "reject", "ops": []}
    if line.lower().startswith("edit"):
        _, _, rest = line.partition(" ")
        idxs = [int(part.strip()) for part in rest.split(",") if part.strip()]
        return {"decision": "approve", "ops": [ops[i] for i in idxs]}
    return {"decision": "approve", "ops": ops}


def _print_last_ai(result: dict, prefix: str) -> None:
    for message in reversed(result.get("messages") or []):
        content = getattr(message, "content", None)
        name = getattr(message, "type", None) or message.__class__.__name__
        if name in {"ai", "AIMessage"} and content:
            print(f"{prefix}> {content}")
            return


def _handle_interrupt(graph, config: dict, payload: dict) -> dict:
    _print_interrupt(payload)
    return graph.invoke(Command(resume=_decision(payload.get("ops") or [])), config)


def _run_until_idle(graph, payload, config: dict, prefix: str) -> None:
    result = graph.invoke(payload, config)
    while True:
        _print_last_ai(result, prefix)
        interrupts = result.get("__interrupt__") or ()
        if not interrupts:
            return
        result = _handle_interrupt(graph, config, interrupts[0].value)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Conversational finance agent. Default is the coordinator. "
            "Kill/restart with the same thread id to resume a paused approval "
            "(Postgres or SQLite file checkpointer)."
        )
    )
    parser.add_argument(
        "thread_id",
        nargs="?",
        help="Reuse this id to resume a paused approval after restart",
    )
    parser.add_argument(
        "--steward",
        action="store_true",
        help="Run the steward graph alone (no coordinator)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    thread_id = args.thread_id or str(uuid.uuid4())
    prefix = "steward" if args.steward else "agent"
    print(
        f"entrypoint={prefix}  thread_id={thread_id}  "
        "(pass this arg to resume after kill/restart; "
        "checkpoints persist on Postgres and on the SQLite file checkpointer)"
    )
    with open_checkpointer() as checkpointer:
        if args.steward:
            graph = build_steward_graph(checkpointer=checkpointer)
        else:
            graph = build_coordinator(checkpointer=checkpointer)
        config = _config(thread_id, steward=args.steward)
        snapshot = graph.get_state(config)
        if snapshot.interrupts:
            payload = snapshot.interrupts[0].value
            _print_interrupt(payload)
            _run_until_idle(
                graph,
                Command(resume=_decision(payload.get("ops") or [])),
                config,
                prefix,
            )
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text or text.lower() in {"quit", "exit"}:
                return
            _run_until_idle(
                graph,
                {"messages": [{"role": "user", "content": text}]},
                config,
                prefix,
            )


if __name__ == "__main__":
    main()
