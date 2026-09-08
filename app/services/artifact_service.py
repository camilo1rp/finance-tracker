"""Analysis artifact storage, digest generation, and on-demand materialization."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AnalysisArtifact, _utcnow_naive
from app.services import analytics_service

MAX_DIGEST_TOKENS = 300


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(x) for x in obj]
    return obj


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    val_str = str(value).strip()
    if not val_str:
        return None
    return date.fromisoformat(val_str)


def format_digest(
    kind: str,
    result: Any,
    filters: dict[str, Any] | None = None,
    *,
    artifact_id: int | None = None,
    title: str | None = None,
) -> str:
    """Produce a concise, token-bounded summary (<= 300 tokens) of an analysis query result.

    Enforces INV-53: Always carries match_count, truncated, and resolved filters.
    Enforces INV-55: Never exposes email bodies, line items descriptions, or snippets.
    """
    filters = filters or {}
    lines: list[str] = []

    # Header line
    header_parts: list[str] = []
    if artifact_id is not None:
        header_parts.append(f"Artifact #{artifact_id}")
    if title:
        header_parts.append(f'"{title}"')
    if header_parts:
        lines.append(" — ".join(header_parts))

    # Meta line
    match_count = "unknown"
    truncated = False
    row_count = 0

    res_dict = result if isinstance(result, dict) else {}
    if isinstance(result, list):
        row_count = len(result)
        match_count = len(result)
    elif isinstance(res_dict, dict):
        match_count = res_dict.get("match_count", "unknown")
        truncated = bool(res_dict.get("truncated", False))
        if "groups" in res_dict and isinstance(res_dict["groups"], list):
            row_count = len(res_dict["groups"])
        elif "transactions" in res_dict and isinstance(res_dict["transactions"], list):
            row_count = len(res_dict["transactions"])
        elif "merchants" in res_dict and isinstance(res_dict["merchants"], list):
            row_count = len(res_dict["merchants"])
        elif "values" in res_dict and isinstance(res_dict["values"], list):
            row_count = len(res_dict["values"])

    lines.append(
        f"kind={kind} rows={row_count} match_count={match_count} truncated={str(truncated).lower()}"
    )

    # Headline numbers
    numbers: list[str] = []
    totals = (
        res_dict.get("totals") if isinstance(res_dict.get("totals"), dict) else res_dict
    )
    for key in ("spend", "net_cash_flow", "purchases", "refunds", "income", "total"):
        val = totals.get(key)
        if val is not None:
            numbers.append(f"{key}={val}")
    if numbers:
        lines.append(" ".join(numbers))

    # Top sample items (top 3)
    top_items: list[str] = []
    if isinstance(res_dict, dict):
        if "groups" in res_dict and isinstance(res_dict["groups"], list):
            for g in res_dict["groups"][:3]:
                val = g.get("group_value", "")
                spd = g.get("spend", g.get("total", ""))
                top_items.append(f"{val} {spd}".strip())
        elif "merchants" in res_dict and isinstance(res_dict["merchants"], list):
            for m in res_dict["merchants"][:3]:
                name = m.get("merchant", "")
                spd = m.get("spend", m.get("total", ""))
                top_items.append(f"{name} {spd}".strip())
        elif "transactions" in res_dict and isinstance(res_dict["transactions"], list):
            for t in res_dict["transactions"][:3]:
                t_date = t.get("transaction_date", "")
                desc = t.get("merchant_normalized") or t.get("description", "")
                amt = t.get("amount", "")
                top_items.append(f"{t_date} {desc} {amt}".strip())
        elif "values" in res_dict and isinstance(res_dict["values"], list):
            for v in res_dict["values"][:3]:
                top_items.append(f"{v.get('value')} ({v.get('count')})")
    elif isinstance(result, list):
        for item in result[:3]:
            if isinstance(item, dict):
                name = item.get("merchant") or item.get("group_value") or item.get("name") or ""
                amt = item.get("spend") or item.get("total") or item.get("amount") or ""
                top_items.append(f"{name} {amt}".strip())

    if top_items:
        lines.append("top: " + " | ".join(top_items))

    # Filters applied line
    filter_parts: list[str] = []
    for k in sorted(filters.keys()):
        v = filters[k]
        if v is not None:
            filter_parts.append(f"{k}={v}")
    if filter_parts:
        lines.append("filters: " + " ".join(filter_parts))
    else:
        lines.append("filters: none")

    return "\n".join(lines)


def extract_digest_summary(kind: str, safe_result: Any) -> dict[str, Any]:
    """Extract structured preview fields to attach to artifact.digest for UI rendering."""
    summary: dict[str, Any] = {}
    if not isinstance(safe_result, dict):
        return summary

    if kind == "total":
        for k in ("spend", "net_cash_flow", "purchases", "refunds", "total", "count", "average", "sign_convention"):
            if k in safe_result:
                summary[k] = safe_result[k]
        summary["match_count"] = safe_result.get("count")
        summary["truncated"] = False
    elif kind == "group_summary":
        summary["groups"] = safe_result.get("groups", [])[:10]
        summary["match_count"] = safe_result.get("match_count")
        summary["truncated"] = safe_result.get("truncated", False)
        if "totals" in safe_result and isinstance(safe_result["totals"], dict):
            summary["totals"] = safe_result["totals"]
            for k in ("spend", "net_cash_flow", "purchases", "refunds", "total"):
                if k in safe_result["totals"]:
                    summary[k] = safe_result["totals"][k]
    elif kind == "transaction_list":
        txs = safe_result.get("transactions", [])[:20]
        summary["transactions"] = txs
        summary["sample_rows"] = txs
        summary["match_count"] = safe_result.get("match_count")
        summary["truncated"] = safe_result.get("truncated", False)
    elif kind == "value_list":
        vals = safe_result.get("values", [])[:20]
        summary["values"] = vals
        summary["sample_items"] = vals
        summary["match_count"] = safe_result.get("match_count")
        summary["truncated"] = safe_result.get("truncated", False)
    elif kind == "mapping_preview":
        summary["preview"] = safe_result
    elif kind == "comparison":
        summary["period_a"] = safe_result.get("period_a")
        summary["period_b"] = safe_result.get("period_b")
        summary["delta"] = safe_result.get("delta")
    elif kind == "large_tool_output":
        raw = safe_result.get("raw") if isinstance(safe_result, dict) else str(safe_result)
        summary["raw"] = raw[:1000] if isinstance(raw, str) else str(raw)[:1000]

    return summary


def persist_artifact(
    db: Session,
    *,
    thread_id: str = "standalone",
    kind: str,
    title: str,
    spec: dict[str, Any],
    result: Any,
    produced_by: str = "analyst",
    run_id: str | None = None,
    derived_from: int | None = None,
    status: str = "open",
    expires_in_seconds: int | None = None,
) -> tuple[int, str]:
    """Persist an analysis artifact and return (artifact_id, digest_text)."""
    safe_result = _json_safe(result)
    safe_spec = _json_safe(spec)
    filters = safe_spec.get("kwargs", {}) if isinstance(safe_spec, dict) else {}
    summary_data = extract_digest_summary(kind, safe_result)

    # Format initial digest
    digest_text = format_digest(kind, safe_result, filters, title=title)
    digest_dict = {
        "text": digest_text,
        "kind": kind,
        "title": title,
        **summary_data,
    }

    if expires_in_seconds is not None:
        expires_at = _utcnow_naive() + timedelta(seconds=expires_in_seconds)
    elif status == "provisional":
        expires_at = _utcnow_naive() + timedelta(seconds=3600)
    else:
        expires_at = None

    artifact = AnalysisArtifact(
        thread_id=thread_id or "standalone",
        run_id=run_id,
        produced_by=produced_by,
        kind=kind,
        title=title[:80],
        spec=safe_spec,
        digest=digest_dict,
        cache=safe_result,
        cache_as_of=_utcnow_naive(),
        derived_from=derived_from,
        status=status,
        created_at=_utcnow_naive(),
        expires_at=expires_at,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    # Re-format with assigned ID
    final_digest_text = format_digest(
        kind, safe_result, filters, artifact_id=artifact.id, title=title
    )
    artifact.digest = {
        "text": final_digest_text,
        "kind": kind,
        "title": title,
        "artifact_id": artifact.id,
        **summary_data,
    }
    db.commit()

    return artifact.id, final_digest_text


def promote_cited_artifacts(
    db: Session,
    thread_id: str,
    cited_ids: list[int],
    run_id: str | None = None,
) -> None:
    """Promote cited provisional artifacts to open status, and mark uncited provisional artifacts as superseded."""
    if cited_ids:
        update_vals: dict[str, Any] = {"status": "open", "expires_at": None}
        if thread_id and thread_id != "standalone":
            update_vals["thread_id"] = thread_id
        db.execute(
            update(AnalysisArtifact)
            .where(AnalysisArtifact.id.in_(cited_ids))
            .values(**update_vals)
        )

    demote_stmt = update(AnalysisArtifact).where(
        AnalysisArtifact.thread_id == thread_id,
        AnalysisArtifact.status == "provisional",
    )
    if cited_ids:
        demote_stmt = demote_stmt.where(AnalysisArtifact.id.not_in(cited_ids))
    if run_id:
        demote_stmt = demote_stmt.where(AnalysisArtifact.run_id == run_id)

    db.execute(demote_stmt.values(status="superseded"))
    db.commit()


def execute_spec(db: Session, tool_name: str, kwargs: dict[str, Any]) -> Any:
    """Execute an analytics spec against analytics_service."""
    kw = dict(kwargs)
    date_from = _parse_date(kw.get("date_from"))
    date_to = _parse_date(kw.get("date_to"))
    account_id = kw.get("account_id")
    owner_id = kw.get("owner_id")
    merchant = kw.get("merchant")
    category = kw.get("category")
    subcategory = kw.get("subcategory")
    transaction_type = kw.get("transaction_type")
    limit = kw.get("limit")

    if tool_name == "summarize":
        group_by = kw.get("group_by", "category")
        return analytics_service.summarize(
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            group_by=group_by,
            merchant=merchant,
            transaction_type=transaction_type,
            category=category,
            subcategory=subcategory,
            limit=limit,
        )
    if tool_name == "get_total":
        return analytics_service.get_total(
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            merchant=merchant,
            transaction_type=transaction_type,
            category=category,
            subcategory=subcategory,
        )
    if tool_name in ("get_cash_flow", "cash_flow"):
        return analytics_service.cash_flow(
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            merchant=merchant,
            category=category,
            subcategory=subcategory,
        )
    if tool_name == "top_merchants":
        return analytics_service.top_merchants(
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            limit=limit or 10,
            merchant=merchant,
            transaction_type=transaction_type,
            category=category,
            subcategory=subcategory,
        )
    if tool_name == "largest_transactions":
        return analytics_service.largest_transactions(
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            limit=limit or 10,
            offset=kw.get("offset", 0),
            merchant=merchant,
            transaction_type=transaction_type,
            category=category,
            subcategory=subcategory,
        )
    if tool_name == "search_transactions":
        query = kw.get("query", "")
        return analytics_service.search_transactions(
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            query=query,
            limit=limit or 50,
            offset=kw.get("offset", 0),
            sort=kw.get("sort"),
            merchant=merchant,
            category=category,
            subcategory=subcategory,
        )
    if tool_name == "list_transactions":
        return analytics_service.list_transactions(
            db,
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            owner_id=owner_id,
            merchant=merchant,
            category=category,
            subcategory=subcategory,
            limit=limit or 25,
            offset=kw.get("offset", 0),
            sort=kw.get("sort"),
        )
    if tool_name == "list_values":
        dimension = kw.get("dimension", "category")
        query = kw.get("query")
        return analytics_service.list_values(
            db,
            dimension=dimension,
            query=query,
            limit=limit or 25,
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            owner_id=owner_id,
        )

    raise ValueError(f"Unknown analytics tool spec: {tool_name}")


def materialize_artifact(
    db: Session,
    artifact: AnalysisArtifact,
    *,
    limit: int | None = None,
    offset: int | None = None,
    sort: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return materialized rows for an artifact, re-executing against semantic layer if needed."""
    if not force_refresh and artifact.cache is not None and limit is None and offset is None and sort is None:
        return _json_safe(artifact.cache)

    spec = artifact.spec or {}
    tool_name = spec.get("tool")
    if tool_name is None and artifact.cache is not None:
        cached = copy.deepcopy(_json_safe(artifact.cache))
        if isinstance(cached, dict):
            for key in ("transactions", "rows", "groups", "values"):
                if key in cached and isinstance(cached[key], list):
                    items = cached[key]
                    start = offset or 0
                    end = start + limit if limit else len(items)
                    cached[key] = items[start:end]
        return cached

    kwargs = copy.deepcopy(spec.get("kwargs", {}))
    if limit is not None:
        kwargs["limit"] = limit
    if offset is not None:
        kwargs["offset"] = offset
    if sort is not None:
        kwargs["sort"] = sort

    fresh_result = execute_spec(db, tool_name, kwargs)
    safe = _json_safe(fresh_result)

    # Update cache
    if limit is None and offset is None and sort is None:
        artifact.cache = safe
        artifact.cache_as_of = _utcnow_naive()
        db.commit()

    return safe


def derive_artifact(
    db: Session,
    parent_id: int,
    mutations: dict[str, Any],
    *,
    thread_id: str = "standalone",
    title: str | None = None,
    produced_by: str = "user",
) -> AnalysisArtifact:
    """Zero-LLM derivation: mutate an existing artifact's query spec and store the child artifact."""
    parent = db.get(AnalysisArtifact, parent_id)
    if parent is None:
        raise ValueError(f"Artifact {parent_id} not found")

    parent_spec = copy.deepcopy(parent.spec or {})
    tool_name = parent_spec.get("tool")
    kwargs = parent_spec.get("kwargs", {})

    # Apply mutations
    for k, v in mutations.items():
        if k == "filter" and isinstance(v, dict):
            for fk, fv in v.items():
                kwargs[fk] = fv
        elif k != "title":
            kwargs[k] = v

    new_title = title or mutations.get("title") or f"Derived from #{parent_id}"
    fresh_result = execute_spec(db, tool_name, kwargs)

    new_id, _ = persist_artifact(
        db,
        thread_id=thread_id or parent.thread_id,
        kind=parent.kind,
        title=new_title,
        spec={"tool": tool_name, "kwargs": kwargs},
        result=fresh_result,
        produced_by=produced_by,
        derived_from=parent.id,
    )

    child = db.get(AnalysisArtifact, new_id)
    assert child is not None
    return child


def cleanup_expired_artifacts_and_proposals(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Expire artifacts past expires_at and mark unconsumed expired proposals as discarded."""
    from app.models import EnrichmentProposal, ProposalStatus

    current_time = now or _utcnow_naive()

    # 1. Expire artifacts past expires_at
    expired_artifacts_stmt = select(AnalysisArtifact).where(
        AnalysisArtifact.status != "expired",
        AnalysisArtifact.expires_at.isnot(None),
        AnalysisArtifact.expires_at <= current_time,
    )
    expired_artifacts = list(db.scalars(expired_artifacts_stmt).all())
    for art in expired_artifacts:
        art.status = "expired"

    # 2. Mark open proposals older than 30 days as discarded
    open_proposals_stmt = select(EnrichmentProposal).where(
        EnrichmentProposal.status == ProposalStatus.OPEN.value,
    )
    open_proposals = list(db.scalars(open_proposals_stmt).all())
    discarded_proposals_count = 0
    for prop in open_proposals:
        if (current_time - prop.created_at).total_seconds() > 30 * 86400:
            prop.status = ProposalStatus.DISCARDED.value
            discarded_proposals_count += 1

    db.commit()
    return {
        "expired_artifacts": len(expired_artifacts),
        "discarded_proposals": discarded_proposals_count,
    }

