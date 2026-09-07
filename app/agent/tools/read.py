"""Read-only agent tools. Each call opens and closes its own DB session."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from sqlalchemy import select

from app.agent.config import tool_session
from app.domain.label_filter import UnknownLabelFilterError
from app.models import Account, AnalysisArtifact, Owner
from app.schemas import (
    AccountOut,
    CashFlowOut,
    GroupSummaryPage,
    MerchantSummary,
    NormalizationMappingOut,
    OwnerOut,
    TotalOut,
    TransactionListOut,
    TransactionOut,
    TransactionPage,
    ValueListOut,
)
from app.services import analytics_service, artifact_service
from app.services.analytics_service import unmapped_summary
from app.services.mapping_query import list_normalization_mappings
from app.services.toon import encode_toon

GroupBy = Literal["category", "owner", "month", "account", "merchant", "subcategory"]
ValueDimension = Literal["category", "subcategory", "merchant"]


def _parse_date(value: str | None) -> date | None:
    if value is None or not str(value).strip():
        return None
    return date.fromisoformat(str(value))


def _dump(models: list) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def _limit_error(limit: int) -> str | None:
    if limit < 1:
        return json.dumps({"error": "limit must be >= 1"})
    return None


def _optional_limit_error(limit: int | None) -> str | None:
    if limit is None:
        return None
    return _limit_error(limit)


def _label_filter_error(exc: UnknownLabelFilterError) -> str:
    return json.dumps({"error": exc.detail})


_COLLECTION_KEYS = ("transactions", "groups", "merchants", "values", "lines")
_MAX_OFFLOAD_UNWRAP_DEPTH = 8


def unwrap_offloaded_payload(data: Any, *, depth: int = 0) -> Any:
    """Unwrap large_tool_output {raw: ...} blobs, including nested JSON escapes."""
    if depth > _MAX_OFFLOAD_UNWRAP_DEPTH:
        return data
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return data
    if any(key in data and isinstance(data[key], list) for key in _COLLECTION_KEYS):
        return data
    raw = data.get("raw")
    if not isinstance(raw, str):
        return data
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"lines": raw.splitlines()}
    if isinstance(parsed, dict):
        return unwrap_offloaded_payload(parsed, depth=depth + 1)
    if isinstance(parsed, list):
        return {"values": parsed}
    return parsed


def slice_artifact_payload(
    data: Any,
    *,
    artifact_id: int,
    kind: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Return a bounded slice of a materialized artifact payload."""
    if isinstance(data, list):
        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "offset": offset,
            "limit": limit,
            "total": len(data),
            "values": data[offset : offset + limit],
        }
    if not isinstance(data, dict):
        return {"artifact_id": artifact_id, "kind": kind, "value": data}
    for key in _COLLECTION_KEYS:
        rows = data.get(key)
        if isinstance(rows, list):
            return {
                "artifact_id": artifact_id,
                "kind": kind,
                "offset": offset,
                "limit": limit,
                "total": len(rows),
                key: rows[offset : offset + limit],
            }
    payload = {"artifact_id": artifact_id, "kind": kind}
    payload.update(data)
    return payload


def _extract_runtime_meta(runtime: ToolRuntime | None) -> tuple[str, str | None]:
    thread_id = "standalone"
    run_id = None
    if runtime and hasattr(runtime, "config") and isinstance(runtime.config, dict):
        cfg = runtime.config
        run_id = cfg.get("run_id")
        configurable = cfg.get("configurable")
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            thread_id = str(configurable["thread_id"])
        elif cfg.get("metadata") and isinstance(cfg["metadata"], dict) and cfg["metadata"].get("thread_id"):
            thread_id = str(cfg["metadata"]["thread_id"])
    return thread_id, run_id


def _emit_artifact_ui_event(
    artifact_id: int,
    kind: str,
    component: str,
    digest: str,
) -> None:
    try:
        from langgraph.graph.ui import push_ui_message

        push_ui_message(
            name=component,
            props={
                "type": "artifact_ref",
                "artifact_id": artifact_id,
                "kind": kind,
                "component": component,
                "digest": {"text": digest},
                "fetch": f"/artifacts/{artifact_id}/rows",
            },
            state_key="ui",
        )
    except Exception:
        pass


@tool
def list_owners() -> str:
    """List registered owners (id and name)."""
    with tool_session() as db:
        rows = db.scalars(select(Owner).order_by(Owner.id)).all()
        return _dump([OwnerOut.model_validate(row) for row in rows])


@tool
def list_accounts() -> str:
    """List accounts (id, name, last4, account_kind, default_owner_id, source_format)."""
    with tool_session() as db:
        rows = db.scalars(select(Account).order_by(Account.id)).all()
        return _dump([AccountOut.model_validate(row) for row in rows])


@tool("list_values", response_format="content_and_artifact")
def list_values(
    dimension: ValueDimension,
    runtime: ToolRuntime,
    query: str | None = None,
    limit: int = 25,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Catalog of distinct effective labels with counts.

    dimension is category, subcategory, or merchant. query is a case-insensitive
    substring. Does not default date_to. Use this before get_total/summarize when
    the stored spelling is unknown. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err, None
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        payload = analytics_service.list_values(
            db,
            dimension,
            query=query,
            limit=limit,
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            account_id=account_id,
            owner_id=owner_id,
        )
        spec = {
            "tool": "list_values",
            "kwargs": {
                "dimension": dimension,
                "query": query,
                "limit": limit,
                "date_from": date_from,
                "date_to": date_to,
                "account_id": account_id,
                "owner_id": owner_id,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="value_list",
            title=f"Values: {dimension}",
            spec=spec,
            result=payload,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "value_list", "grouped_table", digest)
        return digest, {"artifact_id": art_id, "kind": "value_list", "component": "grouped_table"}


@tool
def get_unmapped_values() -> str:
    """Distinct raw type/category/owner/merchant values that still need mapping rules, plus merchants_without_category for rows with no bank category."""
    with tool_session() as db:
        return json.dumps(unmapped_summary(db))


@tool
def list_mappings(
    kind: str | None = None,
    account_id: int | None = None,
    include_global: bool = True,
) -> str:
    """List normalization mapping rules.

    When account_id is set, include_global=true (default) returns that account's
    rules plus global rules (account_id IS NULL). Set include_global=false for
    account-scoped rules only.
    """
    with tool_session() as db:
        rows = list_normalization_mappings(
            db, kind=kind, account_id=account_id, include_global=include_global
        )
        return _dump([NormalizationMappingOut.model_validate(row) for row in rows])


@tool("list_transactions", response_format="content_and_artifact")
def list_transactions(
    runtime: ToolRuntime,
    account_id: int | None = None,
    owner_id: int | None = None,
    category: str | None = None,
    merchant: str | None = None,
    subcategory: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> tuple[str, dict[str, Any] | None]:
    """List compact transaction cards (effective labels, no raw/override triples).

    Does not default date_to. merchant is exact unless it contains `%`.
    category/subcategory must exist on stored transactions. Returns
    {transactions, match_count, returned, truncated}. Use get_transaction for
    triples / raw_type / owner_raw. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err, None
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            payload = analytics_service.list_transactions(
                db,
                date_from=_parse_date(date_from),
                date_to=_parse_date(date_to),
                account_id=account_id,
                owner_id=owner_id,
                merchant=merchant,
                category=category,
                subcategory=subcategory,
                limit=limit,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        spec = {
            "tool": "list_transactions",
            "kwargs": {
                "account_id": account_id,
                "owner_id": owner_id,
                "category": category,
                "merchant": merchant,
                "subcategory": subcategory,
                "date_from": date_from,
                "date_to": date_to,
                "limit": limit,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="transaction_list",
            title="Transaction list",
            spec=spec,
            result=payload,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "transaction_list", "transaction_list", digest)
        return digest, {"artifact_id": art_id, "kind": "transaction_list", "component": "transaction_list"}


@tool
def get_transaction(transaction_id: int) -> str:
    """Full transaction detail: triples, raw_type, owner_raw, and effective labels."""
    with tool_session() as db:
        row = analytics_service.get_transaction(db, transaction_id)
        if row is None:
            return json.dumps({"error": f"transaction {transaction_id} not found"})
        return TransactionOut.model_validate(row).model_dump_json()


@tool("search_transactions", response_format="content_and_artifact")
def search_transactions_tool(
    query: str,
    runtime: ToolRuntime,
    account_id: int | None = None,
    owner_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = 200,
) -> tuple[str, dict[str, Any] | None]:
    """Search payee and label fields (case-insensitive substring).

    query matches description, merchant (raw/normalized/override/effective),
    category triple, subcategory, raw_type, and owner_raw. Optional merchant /
    category / subcategory filters are also contains-matches (not exact labels).
    Returns {totals, transactions, match_count, returned, truncated}. totals
    cover all matches, not just limit. date_to defaults to today. Default
    limit is 200; limit >= 1.
    """
    if err := _limit_error(limit):
        return err, None
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            payload = analytics_service.search_transactions(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                query,
                limit,
                merchant,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        spec = {
            "tool": "search_transactions",
            "kwargs": {
                "query": query,
                "account_id": account_id,
                "owner_id": owner_id,
                "date_from": date_from,
                "date_to": date_to,
                "merchant": merchant,
                "category": category,
                "subcategory": subcategory,
                "limit": limit,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="transaction_list",
            title=f"Search: {query}",
            spec=spec,
            result=payload,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "transaction_list", "transaction_list", digest)
        return digest, {"artifact_id": art_id, "kind": "transaction_list", "component": "transaction_list"}


@tool("summarize", response_format="content_and_artifact")
def summarize(
    group_by: GroupBy,
    runtime: ToolRuntime,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Group transaction totals with the same breakdown as get_total per bucket.

    Returns {groups, match_count, returned, truncated}. group_by is category,
    owner, month, account, merchant, or subcategory. Month buckets are YYYY-MM,
    ascending; other groupings sort by spend desc. limit caps groups after sort
    (top N by spend, or first N months). date_to defaults to today. merchant
    is exact unless it contains `%`.
    """
    if err := _optional_limit_error(limit):
        return err, None
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            payload = analytics_service.summarize(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                group_by,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
                limit=limit,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        comp = "timeseries" if group_by == "month" else "grouped_table"
        spec = {
            "tool": "summarize",
            "kwargs": {
                "group_by": group_by,
                "date_from": date_from,
                "date_to": date_to,
                "account_id": account_id,
                "owner_id": owner_id,
                "merchant": merchant,
                "transaction_type": transaction_type,
                "category": category,
                "subcategory": subcategory,
                "limit": limit,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="group_summary",
            title=f"Summary by {group_by}",
            spec=spec,
            result=payload,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "group_summary", comp, digest)
        return digest, {"artifact_id": art_id, "kind": "group_summary", "component": comp}


@tool("get_total", response_format="content_and_artifact")
def get_total(
    runtime: ToolRuntime,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Return per-type magnitudes, net spending, and net cash flow.

    Type SPEND means purchases/charges, not household spending.
    `purchases` = SPEND bucket; `refunds` = REFUND bucket;
    `spend`/`total` = purchases − refunds; `count`/`average` over purchase
    rows. `net_cash_flow` = income + refunds − purchases − fees (same as get_cash_flow).
    `by_type` lists every effective type (abs amounts). If transaction_type
    is set, `total`/`count`/`average` match that type instead.
    sign_convention (when account_id is set) is CSV import convention.
    date_to defaults to today. merchant is exact unless it contains `%`.
    Amounts are positive magnitudes except net_cash_flow, which can be negative.
    """
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            row = analytics_service.get_total(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        spec = {
            "tool": "get_total",
            "kwargs": {
                "date_from": date_from,
                "date_to": date_to,
                "account_id": account_id,
                "owner_id": owner_id,
                "merchant": merchant,
                "transaction_type": transaction_type,
                "category": category,
                "subcategory": subcategory,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="total",
            title="Totals breakdown",
            spec=spec,
            result=row,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "total", "kpi_row", digest)
        return digest, {"artifact_id": art_id, "kind": "total", "component": "kpi_row"}


@tool("top_merchants", response_format="content_and_artifact")
def top_merchants(
    runtime: ToolRuntime,
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Top merchants by net spend (purchases − refunds) with get_total breakdown.

    Each row includes the same fields as get_total plus merchant.
    date_to defaults to today, limit=10. merchant filter is exact unless it
    contains `%`. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err, None
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            rows = analytics_service.top_merchants(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                limit,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        spec = {
            "tool": "top_merchants",
            "kwargs": {
                "limit": limit,
                "date_from": date_from,
                "date_to": date_to,
                "account_id": account_id,
                "owner_id": owner_id,
                "merchant": merchant,
                "transaction_type": transaction_type,
                "category": category,
                "subcategory": subcategory,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="group_summary",
            title="Top merchants",
            spec=spec,
            result={"merchants": rows, "match_count": len(rows), "returned": len(rows), "truncated": False},
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "group_summary", "bar", digest)
        return digest, {"artifact_id": art_id, "kind": "group_summary", "component": "bar"}


@tool("largest_transactions", response_format="content_and_artifact")
def largest_transactions(
    runtime: ToolRuntime,
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Largest transactions by absolute amount with filter-scoped totals.

    Returns {totals, transactions, match_count, returned, truncated}.
    totals use get_total field meanings over the same window. The list
    includes all types unless transaction_type is set. Cards are compact;
    use get_transaction for triples. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err, None
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            payload = analytics_service.largest_transactions(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                limit,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        spec = {
            "tool": "largest_transactions",
            "kwargs": {
                "limit": limit,
                "date_from": date_from,
                "date_to": date_to,
                "account_id": account_id,
                "owner_id": owner_id,
                "merchant": merchant,
                "transaction_type": transaction_type,
                "category": category,
                "subcategory": subcategory,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="transaction_list",
            title="Largest transactions",
            spec=spec,
            result=payload,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "transaction_list", "transaction_list", digest)
        return digest, {"artifact_id": art_id, "kind": "transaction_list", "component": "transaction_list"}


@tool("get_cash_flow", response_format="content_and_artifact")
def get_cash_flow(
    runtime: ToolRuntime,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Household cash-flow with the same core totals as get_total plus extras.

    Includes by_type, purchases, refunds, spend (purchases − refunds),
    net_cash_flow, total, count, average, plus income, fees, transfers,
    other, and other_count. net_cash_flow excludes transfers and other.
    Depository outflows that fund card payments may appear as SPEND until
    overridden — prefer account_id for a single-account view.
    merchant is exact unless it contains `%`.
    """
    thread_id, run_id = _extract_runtime_meta(runtime)
    with tool_session() as db:
        try:
            row = analytics_service.cash_flow(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                merchant,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc), None
        spec = {
            "tool": "get_cash_flow",
            "kwargs": {
                "date_from": date_from,
                "date_to": date_to,
                "account_id": account_id,
                "owner_id": owner_id,
                "merchant": merchant,
                "category": category,
                "subcategory": subcategory,
            },
        }
        art_id, digest = artifact_service.persist_artifact(
            db,
            thread_id=thread_id,
            run_id=run_id,
            kind="total",
            title="Cash flow summary",
            spec=spec,
            result=row,
            produced_by="analyst",
        )
        _emit_artifact_ui_event(art_id, "total", "kpi_row", digest)
        return digest, {"artifact_id": art_id, "kind": "total", "component": "kpi_row"}


@tool("open_artifact")
def open_artifact(
    artifact_id: int,
    runtime: ToolRuntime,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Retrieve a bounded TOON slice of an existing artifact by ID.

    Use this just-in-time retrieval tool when you need to inspect raw transaction
    rows or group records from a previous analysis result. Opening a
    large_tool_output artifact returns a sliced subset of the stored payload.
    """
    if limit < 1 or limit > 50:
        limit = 20
    if offset < 0:
        offset = 0
    with tool_session() as db:
        artifact = db.get(AnalysisArtifact, artifact_id)
        if artifact is None:
            return encode_toon({"error": f"artifact {artifact_id} not found"})
        data = unwrap_offloaded_payload(artifact_service.materialize_artifact(db, artifact))
        payload = slice_artifact_payload(
            data,
            artifact_id=artifact_id,
            kind=artifact.kind,
            offset=offset,
            limit=limit,
        )
        return encode_toon(payload)


@tool("submit_analysis", return_direct=True)
def submit_analysis(
    artifact_ids: list[int],
    narrative: str,
    runtime: ToolRuntime,
) -> Command:
    """Submit the final analysis findings and finish the analyst turn.

    Takes a list of referenced artifact_ids and a concise narrative summary (<= 600 characters).
    This is the analyst's only finish.
    """
    ids_str = ", ".join(str(i) for i in artifact_ids)
    summary_text = f"artifacts: [{ids_str}]. {narrative}".strip()
    return Command(
        update={
            "artifact_ids": artifact_ids,
            "narrative": narrative,
            "messages": [
                ToolMessage(
                    content=summary_text,
                    tool_call_id=runtime.tool_call_id or "",
                )
            ],
        }
    )


CATALOG_TOOLS = [
    list_owners,
    list_accounts,
    list_values,
]

READ_TOOLS = [
    *CATALOG_TOOLS,
    get_unmapped_values,
    list_mappings,
    list_transactions,
    get_transaction,
    search_transactions_tool,
]

ANALYTICS_TOOLS = [
    summarize,
    get_total,
    get_cash_flow,
    top_merchants,
    largest_transactions,
]

ANALYST_TOOLS = [
    *CATALOG_TOOLS,
    list_transactions,
    get_transaction,
    search_transactions_tool,
    open_artifact,
    submit_analysis,
    *ANALYTICS_TOOLS,
]
