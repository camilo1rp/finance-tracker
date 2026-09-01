"""Tiny REPL to run the steward against a thread id."""
from __future__ import annotations

import sys
import uuid

from langgraph.types import Command

from app.agent.config import open_checkpointer
from app.agent.steward_graph import build_steward_graph

RECURSION_LIMIT = 25


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def _print_interrupt(payload: dict) -> None:
    print("\n=== Approval needed (nothing applied yet) ===")
    print(f"Rationale: {payload.get('rationale') or '(none)'}")
    rules = payload.get("rules") or []
    print("Rules:")
    for i, rule in enumerate(rules):
        scope = f"account={rule.get('account_id')}" if rule.get("account_id") else "global"
        merchant = f" merchant={rule.get('merchant')}" if rule.get("merchant") else "all merchants"
        print(
            f"  [{i}] {rule.get('kind')}  {rule.get('raw_value')!r} -> "
            f"{rule.get('canonical_value')!r}  ({scope}, {merchant})"
        )
    preview = payload.get("preview") or {}
    print(
        f"Preview: scanned={preview.get('scanned')}  "
        f"would_change={preview.get('total_would_change')}  "
        f"errors={preview.get('validation_errors')}"
    )
    for impact in preview.get("rules") or []:
        rule = impact.get("rule") or {}
        print(
            f"  {rule.get('raw_value')!r}: would_change={impact.get('would_change')} "
            f"shadowed={impact.get('shadowed_by_existing')} "
            f"suppressed={impact.get('suppressed_by_override')} "
            f"duplicate_of={impact.get('duplicate_of_existing_id')}"
        )
    print("Type: approve | reject | edit 0,2")


def _decision(rules: list) -> dict:
    line = input("decision> ").strip()
    if line.lower().startswith("reject"):
        return {"decision": "reject", "rules": []}
    if line.lower().startswith("edit"):
        _, _, rest = line.partition(" ")
        idxs = [int(part.strip()) for part in rest.split(",") if part.strip()]
        return {"decision": "approve", "rules": [rules[i] for i in idxs]}
    return {"decision": "approve", "rules": rules}


def _print_last_ai(result: dict) -> None:
    for message in reversed(result.get("messages") or []):
        content = getattr(message, "content", None)
        name = getattr(message, "type", None) or message.__class__.__name__
        if name in {"ai", "AIMessage"} and content:
            print(f"steward> {content}")
            return


def _handle_interrupt(graph, config: dict, payload: dict) -> dict:
    _print_interrupt(payload)
    return graph.invoke(Command(resume=_decision(payload.get("rules") or [])), config)


def _run_until_idle(graph, payload, config: dict) -> None:
    result = graph.invoke(payload, config)
    while True:
        _print_last_ai(result)
        interrupts = result.get("__interrupt__") or ()
        if not interrupts:
            return
        result = _handle_interrupt(graph, config, interrupts[0].value)


def main() -> None:
    thread_id = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())
    print(f"thread_id={thread_id}  (pass this arg to resume after restart)")
    with open_checkpointer() as checkpointer:
        graph = build_steward_graph(checkpointer=checkpointer)
        config = _config(thread_id)
        snapshot = graph.get_state(config)
        if snapshot.interrupts:
            payload = snapshot.interrupts[0].value
            _print_interrupt(payload)
            _run_until_idle(
                graph,
                Command(resume=_decision(payload.get("rules") or [])),
                config,
            )
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text or text.lower() in {"quit", "exit"}:
                return
            _run_until_idle(graph, {"messages": [{"role": "user", "content": text}]}, config)


if __name__ == "__main__":
    main()
