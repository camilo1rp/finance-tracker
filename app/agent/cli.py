"""REPL for the coordinator (default) or the standalone steward."""
from __future__ import annotations

import argparse
from datetime import date
import uuid

from langgraph.types import Command
from sqlalchemy import select

from app.agent.config import (
    email_max_candidates,
    email_lookahead_days,
    email_lookback_days,
    email_provider,
    email_source_from_env,
    extractor_from_env,
    get_tool_session_factory,
    open_checkpointer,
)
from app.agent.coordinator import build_coordinator
from app.agent.enricher_graph import build_enricher_graph
from app.agent.steward_graph import build_steward_graph
from app.agent.tools.subagents import _enricher_summary
from app.services.enrichment_service import (
    EnrichmentConfig,
    enrich_range,
    find_candidates,
    seed_merchant_senders,
)
from app.models import Transaction

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
    if kind_of == "set_transaction_category":
        return (
            f"set transaction {op.get('transaction_id')} category -> "
            f"{op.get('category')!r}"
        )
    if kind_of == "remove_transaction_override":
        return f"remove transaction {op.get('transaction_id')} override"
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
    grouped: dict[str, list[int]] = {
        "update": [],
        "delete": [],
        "create": [],
        "set_transaction_category": [],
        "remove_transaction_override": [],
    }
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
        ("Set Overrides", "set_transaction_category"),
        ("Remove Overrides", "remove_transaction_override"),
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
        f"errors={preview.get('validation_errors')}  "
        f"overrides={preview.get('overrides')}"
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
    parser.add_argument(
        "--enricher",
        metavar="TASK",
        help="Run the enricher graph alone (no coordinator) for this task",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Run email enrichment over a transaction date range",
    )
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed-senders", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _run_enrich(args: argparse.Namespace) -> int:
    if not args.date_from or not args.date_to:
        raise SystemExit("--from and --to are required with --enrich")
    source = email_source_from_env()
    if source is None:
        print(f"EMAIL_PROVIDER={email_provider()} produced no email source")
        return 1
    extractor = extractor_from_env()
    session_factory = get_tool_session_factory()
    parsed_from = date.fromisoformat(args.date_from)
    parsed_to = date.fromisoformat(args.date_to)
    if args.seed_senders:
        with session_factory() as db:
            inserted = seed_merchant_senders(db)
            db.commit()
        print(f"seeded_merchant_senders={inserted}")
    config = EnrichmentConfig(
        lookback_days=email_lookback_days(),
        lookahead_days=email_lookahead_days(),
        max_candidates=email_max_candidates(),
        force=args.force,
        allow_text_hint=getattr(source, "allowlist", []) == ["*"],
    )
    if args.dry_run:
        with session_factory() as db:
            txn_ids = list(
                db.scalars(
                    select(Transaction.id).where(
                        Transaction.is_spend.is_(True),
                        Transaction.transaction_date >= parsed_from,
                        Transaction.transaction_date <= parsed_to,
                    )
                ).all()
            )
            for txn_id in txn_ids:
                txn = db.get(Transaction, txn_id)
                if txn is None:
                    continue
                refs = find_candidates(
                    db,
                    source,
                    txn,
                    config.lookback_days,
                    config.lookahead_days,
                    config.allow_text_hint,
                )
                print(f"transaction_id={txn.id} candidates={len(refs)}")
        return 0
    report = enrich_range(
        session_factory,
        source,
        extractor,
        parsed_from,
        parsed_to,
        config,
    )
    print(
        " ".join(
            [
                f"scanned={report.scanned}",
                f"enriched={report.enriched}",
                f"unmatched={report.unmatched}",
                f"skipped={report.skipped}",
                f"failed={report.failed}",
                f"duration_s={report.duration_s}",
            ]
        )
    )
    for outcome in report.outcomes:
        if outcome.error_class:
            print(
                f"transaction_id={outcome.transaction_id} error_class={outcome.error_class}"
            )
    return 0


def _run_enricher(task: str) -> int:
    graph = build_enricher_graph()
    result = graph.invoke(
        {"messages": [{"role": "user", "content": task}]},
        {"recursion_limit": 15},
    )
    print(_enricher_summary(result))
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.enricher:
        raise SystemExit(_run_enricher(args.enricher))
    if args.enrich:
        raise SystemExit(_run_enrich(args))
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
