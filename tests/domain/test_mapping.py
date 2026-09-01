import pytest

from app.domain.mapping import ImportMapping, SignConvention, resolve_mapping


def test_mapping_requires_type_col_or_sign_convention() -> None:
    with pytest.raises(ValueError, match="type_col or sign_convention"):
        ImportMapping(date_col="Date", description_col="Desc", amount_col="Amount")


def test_mapping_accepts_type_col_without_sign() -> None:
    mapping = ImportMapping(
        date_col="Date",
        description_col="Desc",
        amount_col="Amount",
        type_col="Type",
    )
    assert mapping.type_col == "Type"


def test_mapping_accepts_sign_convention_without_type_col() -> None:
    mapping = ImportMapping(
        date_col="Date",
        description_col="Desc",
        amount_col="Amount",
        sign_convention=SignConvention.NEGATIVE_IS_SPEND,
    )
    assert mapping.sign_convention is SignConvention.NEGATIVE_IS_SPEND


def test_resolve_mapping_ignores_override_for_now() -> None:
    default = ImportMapping(
        date_col="Date",
        description_col="Desc",
        amount_col="Amount",
        type_col="Type",
    )
    override = ImportMapping(
        date_col="Transaction Date",
        description_col="Desc",
        amount_col="Amount",
        type_col="Type",
    )
    assert resolve_mapping(default, override) is default
    assert resolve_mapping(default, None) is default
