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
from app.database import init_db
from app.integrations.gmail_common.query import build_search_query
from app.models import Transaction
from app.services.enrichment_service import (
    EnrichmentConfig,
    InspectReport,
    _merchant_key_for_transaction,
    enrich_range,
    inspect_transaction,
    plan_candidate_search,
    reset_learned_senders,
    seed_merchant_senders,
)

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
    parser.add_argument(
        "--seed-senders",
        action="store_true",
        help="Accepted for compatibility; seeding already runs on every --enrich",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--inspect",
        type=int,
        metavar="TRANSACTION_ID",
        help="Fetch candidates for one transaction and print inspection stats (no evidence writes)",
    )
    parser.add_argument(
        "--ids",
        help="Comma-separated transaction ids to restrict enrichment (dry-run and real)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="With --dry-run, print the Gmail query string per transaction",
    )
    parser.add_argument(
        "--reset-learned",
        action="store_true",
        help="Delete merchant_senders rows with origin=learned and exit",
    )
    return parser.parse_args(argv)


def _parse_txn_ids(raw: str | None) -> list[int] | None:
    if raw is None or not str(raw).strip():
        return None
    return [int(part.strip()) for part in str(raw).split(",") if part.strip()]


def _reset_learned() -> int:
    session_factory = get_tool_session_factory()
    init_db()
    with session_factory() as db:
        deleted = reset_learned_senders(db)
        db.commit()
    print(f"deleted_learned={deleted}")
    return 0


def _print_inspect(report: InspectReport) -> None:
    if not report.found:
        print(f"transaction_id={report.transaction_id} not_found")
        return
    print(
        f"transaction_id={report.transaction_id} "
        f"resolution={report.resolution} "
        f"candidates={len(report.candidates)}"
    )
    for item in report.candidates:
        print(
            f"external_ref={item.external_ref} "
            f"body_source={item.body_source} "
            f"body_bytes={item.body_bytes} "
            f"plain_bytes={item.plain_bytes} "
            f"html_text_bytes={item.html_text_bytes} "
            f"truncated={item.truncated} "
            f"currency_tokens={item.currency_token_count} "
            f"date_tokens={item.date_token_count} "
            f"total={item.total} "
            f"order_date={item.order_date.isoformat() if item.order_date is not None else None} "
            f"order_id={item.order_id} "
            f"raw_confidence={item.raw_confidence} "
            f"product_type={item.product_type} "
            f"category_hint={item.category_hint}"
        )


def _run_enrich(args: argparse.Namespace) -> int:
    inspect_id = getattr(args, "inspect", None)
    id_filter = _parse_txn_ids(getattr(args, "ids", None))
    parsed_from = date.fromisoformat(args.date_from) if args.date_from else None
    parsed_to = date.fromisoformat(args.date_to) if args.date_to else None
    if (
        inspect_id is None
        and id_filter is None
        and (parsed_from is None or parsed_to is None)
    ):
        raise SystemExit("--from and --to are required with --enrich")
    source = email_source_from_env()
    if source is None:
        print(f"EMAIL_PROVIDER={email_provider()} produced no email source")
        return 1
    status = source.health()
    if not status.available:
        print(status.detail or "unavailable")
        return 1
    extractor = extractor_from_env()
    session_factory = get_tool_session_factory()
    init_db()
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
    if inspect_id is not None:
        with session_factory() as db:
            report = inspect_transaction(db, source, extractor, inspect_id, config)
        _print_inspect(report)
        return 0 if report.found else 1
    if args.dry_run:
        with session_factory() as db:
            stmt = select(Transaction.id).where(Transaction.is_spend.is_(True))
            if parsed_from is not None:
                stmt = stmt.where(Transaction.transaction_date >= parsed_from)
            if parsed_to is not None:
                stmt = stmt.where(Transaction.transaction_date <= parsed_to)
            if id_filter is not None:
                stmt = stmt.where(Transaction.id.in_(id_filter))
            txn_ids = list(db.scalars(stmt.order_by(Transaction.id)).all())
            for txn_id in txn_ids:
                txn = db.get(Transaction, txn_id)
                if txn is None:
                    continue
                resolution, query = plan_candidate_search(
                    db,
                    txn,
                    config.lookback_days,
                    config.lookahead_days,
                    config.allow_text_hint,
                )
                refs = source.search(query) if query is not None else []
                merchant = _merchant_key_for_transaction(db, txn) or ""
                line = (
                    f"transaction_id={txn.id} "
                    f'merchant="{merchant[:40]}" '
                    f"resolution={resolution} "
                    f"candidates={len(refs)}"
                )
                if args.verbose:
                    gmail_query = build_search_query(query) if query is not None else ""
                    line = f"{line} gmail_query={gmail_query}"
                print(line)
        return 0
    report = enrich_range(
        session_factory,
        source,
        extractor,
        parsed_from,
        parsed_to,
        config,
        txn_ids=id_filter,
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
    if getattr(args, "inspect", None) is not None and not args.enrich:
        raise SystemExit("--inspect requires --enrich")
    if args.reset_learned:
        raise SystemExit(_reset_learned())
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
