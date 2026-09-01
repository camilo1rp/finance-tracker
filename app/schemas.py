"""
Pydantic models -- the API's request/response contract.
"""
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


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
    merchant: Optional[str] = None  # category only; None = all merchants


class NormalizationMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    raw_value: str
    canonical_value: str
    account_id: Optional[int]
    merchant: Optional[str] = None


MappingKind = Literal["transaction_type", "category", "owner", "merchant"]


class ProposedMappingIn(BaseModel):
    kind: MappingKind
    raw_value: str
    canonical_value: str
    account_id: Optional[int] = None
    merchant: Optional[str] = None  # only valid when kind == "category"


class SampleChange(BaseModel):
    transaction_id: int
    description: str
    field: MappingKind
    current_effective: Optional[str]
    new_effective: Optional[str]


class RuleImpact(BaseModel):
    rule: ProposedMappingIn
    would_change: int
    suppressed_by_override: int
    shadowed_by_existing: int
    duplicate_of_existing_id: Optional[int]
    samples: list[SampleChange]


class MappingPreview(BaseModel):
    scanned: int
    total_would_change: int
    rules: list[RuleImpact]
    validation_errors: list[str]


class MappingPreviewIn(BaseModel):
    rules: list[ProposedMappingIn]
    account_id: Optional[int] = None


class ApplyMappingPlanIn(BaseModel):
    rules: list[ProposedMappingIn]
    account_id: Optional[int] = None


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
    default_owner_id: Optional[int] = None
    source_format: str = "csv"
    default_mapping: ImportMappingIn


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    last4: str
    default_owner_id: Optional[int]
    source_format: str


# ---- Imports ----

class UnmappedValuesOut(BaseModel):
    transaction_types: list[str]
    categories: list[str]
    owners: list[str]
    merchants: list[str] = []


class ApplyResult(BaseModel):
    created_mapping_ids: list[int]
    skipped_duplicates: list[ProposedMappingIn]
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


# ---- Analytics ----

class GroupSummary(BaseModel):
    group_value: str
    total: Decimal
    count: int


class TotalOut(BaseModel):
    total: Decimal
    count: int
    average: Decimal


class MerchantSummary(BaseModel):
    merchant: str
    total: Decimal
    count: int
