"""
Pydantic models -- the API's request/response contract.
"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


# ---- Owners ----

class OwnerCreate(BaseModel):
    name: str


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


# ---- Normalization mappings ----

class NormalizationMappingCreate(BaseModel):
    kind: str  # "transaction_type" | "category" | "owner" | "merchant"
    raw_value: str
    canonical_value: str
    account_id: Optional[int] = None  # None = global rule
    merchant: Optional[str] = None  # category / transaction_type; None = all merchants


class NormalizationMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    raw_value: str
    canonical_value: str
    account_id: Optional[int]
    merchant: Optional[str] = None


MappingKind = Literal["transaction_type", "category", "owner", "merchant"]


class CreateMappingOp(BaseModel):
    op: Literal["create"] = "create"
    kind: MappingKind
    raw_value: str
    canonical_value: str
    account_id: Optional[int] = None
    merchant: Optional[str] = None  # category or transaction_type; None = all merchants


class UpdateMappingOp(BaseModel):
    op: Literal["update"] = "update"
    mapping_id: int
    canonical_value: str


class DeleteMappingOp(BaseModel):
    op: Literal["delete"] = "delete"
    mapping_id: int


class SetTransactionCategoryOp(BaseModel):
    op: Literal["set_transaction_category"] = "set_transaction_category"
    transaction_id: int
    category: str
    evidence_ids: list[int] = []
    rationale: Optional[str] = None

    @field_validator("category")
    @classmethod
    def _category_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("category must not be empty")
        return value


class RemoveTransactionOverrideOp(BaseModel):
    op: Literal["remove_transaction_override"] = "remove_transaction_override"
    transaction_id: int
    rationale: Optional[str] = None


MappingOp = Annotated[
    Union[
        CreateMappingOp,
        UpdateMappingOp,
        DeleteMappingOp,
        SetTransactionCategoryOp,
        RemoveTransactionOverrideOp,
    ],
    Field(discriminator="op"),
]
_MAPPING_OP_ADAPTER = TypeAdapter(MappingOp)


def parse_mapping_op(data: MappingOp | dict) -> MappingOp:
    if isinstance(
        data,
        (
            CreateMappingOp,
            UpdateMappingOp,
            DeleteMappingOp,
            SetTransactionCategoryOp,
            RemoveTransactionOverrideOp,
        ),
    ):
        return data
    return _MAPPING_OP_ADAPTER.validate_python(data)


class MappingPlanIn(BaseModel):
    ops: list[MappingOp]
    account_id: Optional[int] = None  # reclassify / preview scan scope


class MappingPatchIn(BaseModel):
    canonical_value: str


class SampleChange(BaseModel):
    transaction_id: int
    description: str
    field: MappingKind
    current_effective: Optional[str]
    new_effective: Optional[str]


class FallbackCount(BaseModel):
    mapping_id: int
    count: int


class OpImpact(BaseModel):
    index: int
    op: MappingOp
    would_change: int = 0
    suppressed_by_override: int = 0
    shadowed_by_existing: int = 0
    duplicate_of_existing_id: Optional[int] = None
    conflicts_with_existing_id: Optional[int] = None
    existing_canonical: Optional[str] = None
    old_canonical: Optional[str] = None
    new_canonical: Optional[str] = None
    falls_back_to: list[FallbackCount] = []
    would_become_unmapped: int = 0
    samples: list[SampleChange] = []


class OverridePreview(BaseModel):
    transaction_id: int
    exists: bool
    current_override: Optional[str] = None
    current_effective_category: Optional[str] = None
    proposed: Optional[str] = None
    action: Literal["set", "replace_conflict", "noop", "remove", "remove_noop", "missing"]
    evidence_ids: list[int] = []


class MappingPreview(BaseModel):
    scanned: int
    total_would_change: int
    ops: list[OpImpact]
    overrides: list[OverridePreview] = []
    validation_errors: list[str]


class MappingPatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    raw_value: str
    canonical_value: str
    account_id: Optional[int]
    merchant: Optional[str] = None
    reclass_scanned: int
    reclass_updated: int


class MappingDeleteOut(BaseModel):
    deleted_id: int
    reclass_scanned: int
    reclass_updated: int


# ---- Accounts ----

class ImportMappingIn(BaseModel):
    date_col: str
    description_col: str
    amount_col: str
    category_col: Optional[str] = None
    owner_col: Optional[str] = None
    type_col: Optional[str] = None
    merchant_col: Optional[str] = None
    sign_convention: Optional[str] = None


class AccountCreate(BaseModel):
    name: str
    last4: str
    account_kind: str
    default_owner_id: Optional[int] = None
    source_format: str = "csv"
    default_mapping: ImportMappingIn


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    last4: str
    account_kind: str
    default_owner_id: Optional[int]
    source_format: str


# ---- Imports ----

class UnmappedValuesOut(BaseModel):
    transaction_types: list[str]
    categories: list[str]
    owners: list[str]
    merchants: list[str] = []


class SkippedOp(BaseModel):
    op: MappingOp
    reason: Literal["duplicate", "missing"]


class ApplyResult(BaseModel):
    created_ids: list[int]
    updated_ids: list[int]
    deleted_ids: list[int]
    skipped: list[SkippedOp]
    overrides_set: int = 0
    overrides_removed: int = 0
    reclass_scanned: int
    reclass_updated: int
    unmapped_after: UnmappedValuesOut


class ImportResult(BaseModel):
    account_id: int
    import_batch_id: Optional[int]
    total_rows_read: int
    inserted: int
    duplicates_skipped: int
    unmapped: UnmappedValuesOut
    errors: list[str]


# ---- Transactions ----

class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    transaction_date: date
    description: str
    amount: Decimal
    transaction_type: str
    type_override: Optional[str] = None
    is_spend: bool
    category_raw: Optional[str]
    category_normalized: Optional[str]
    category_override: Optional[str]
    owner_id: Optional[int]
    merchant_raw: Optional[str] = None
    merchant_normalized: Optional[str] = None
    merchant_override: Optional[str] = None


class ReclassifyResultOut(BaseModel):
    scanned: int
    updated: int
    unmapped: UnmappedValuesOut


class TransactionPatch(BaseModel):
    category_override: Optional[str] = None
    owner_id: Optional[int] = None
    merchant_override: Optional[str] = None
    type_override: Optional[str] = None


class TypeTotalOut(BaseModel):
    transaction_type: str
    total: Decimal
    count: int


class TotalsBreakdown(BaseModel):
    by_type: list[TypeTotalOut]
    purchases: Decimal
    refunds: Decimal
    spend: Decimal
    net_cash_flow: Decimal
    total: Decimal
    count: int
    average: Decimal
    sign_convention: Optional[str] = None


class TotalOut(TotalsBreakdown):
    pass


class GroupSummary(TotalsBreakdown):
    group_value: str


class MerchantSummary(TotalsBreakdown):
    merchant: str


class CashFlowOut(TotalsBreakdown):
    income: Decimal
    fees: Decimal
    transfers: Decimal
    other: Decimal
    other_count: int


class TransactionListOut(BaseModel):
    totals: TotalOut
    transactions: list[TransactionOut]
