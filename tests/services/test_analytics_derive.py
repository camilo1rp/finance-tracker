from decimal import Decimal

from app.services.analytics_service import _derive_totals


def test_derive_totals_empty() -> None:
    result = _derive_totals([])
    assert result["purchases"] == Decimal("0.00")
    assert result["refunds"] == Decimal("0.00")
    assert result["spend"] == Decimal("0.00")
    assert result["net_cash_flow"] == Decimal("0.00")
    assert result["total"] == Decimal("0.00")
    assert result["count"] == 0
    assert result["average"] == Decimal("0.00")
    assert result["by_type"] == []


def test_derive_totals_refunds_reduce_spend() -> None:
    by_type = [
        {"transaction_type": "SPEND", "total": Decimal("20.00"), "count": 2},
        {"transaction_type": "REFUND", "total": Decimal("5.00"), "count": 1},
    ]
    result = _derive_totals(by_type)
    assert result["purchases"] == Decimal("20.00")
    assert result["refunds"] == Decimal("5.00")
    assert result["spend"] == Decimal("15.00")
    assert result["total"] == Decimal("15.00")
    assert result["count"] == 2


def test_derive_totals_net_cash_flow_formula() -> None:
    by_type = [
        {"transaction_type": "SPEND", "total": Decimal("10.00"), "count": 1},
        {"transaction_type": "INCOME", "total": Decimal("100.00"), "count": 1},
        {"transaction_type": "REFUND", "total": Decimal("5.00"), "count": 1},
        {"transaction_type": "FEE", "total": Decimal("2.00"), "count": 1},
    ]
    result = _derive_totals(by_type)
    assert result["net_cash_flow"] == Decimal("93.00")


def test_derive_totals_transaction_type_headline() -> None:
    by_type = [
        {"transaction_type": "SPEND", "total": Decimal("10.00"), "count": 1},
        {"transaction_type": "INCOME", "total": Decimal("100.00"), "count": 1},
    ]
    result = _derive_totals(by_type, transaction_type="INCOME")
    assert result["total"] == Decimal("100.00")
    assert result["count"] == 1
    assert result["average"] == Decimal("100.00")
    assert result["spend"] == Decimal("10.00")
