from typing import NotRequired

from langchain.agents import AgentState
from pydantic import BaseModel, Field

from app.schemas import CreateMappingOp


class ProposedOverride(BaseModel):
    transaction_id: int
    category: str
    evidence_ids: list[int] = Field(min_length=1)
    confidence: float
    rationale: str = Field(max_length=240)


class UnresolvedTransaction(BaseModel):
    transaction_id: int
    reason: str


class EnrichmentRecommendation(BaseModel):
    proposed_overrides: list[ProposedOverride]
    merchant_rule_suggestions: list[CreateMappingOp]
    unresolved: list[UnresolvedTransaction]
    narrative: str = Field(max_length=600)
    new_categories: list[str] = []


class StewardState(AgentState):
    proposed_ops: NotRequired[list[dict]]
    account_scope: NotRequired[int | None]
    pending_preview: NotRequired[dict | None]
    apply_result: NotRequired[dict | None]
    rationale: NotRequired[str | None]


class EnricherState(AgentState):
    recommendation: NotRequired[dict]
    proposal_id: NotRequired[int]
