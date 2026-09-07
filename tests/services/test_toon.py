from decimal import Decimal

from app.services.toon import encode_toon


def test_encode_toon_uniform_objects_and_quoted_comma() -> None:
    text = encode_toon(
        {
            "artifact_id": 3,
            "kind": "transaction_list",
            "offset": 0,
            "limit": 2,
            "total": 2,
            "transactions": [
                {"id": 1, "description": "COFFEE", "amount": "-4.50"},
                {"id": 2, "description": "SHOP, MAIN", "amount": "-10.00"},
            ],
        }
    )
    assert "artifact_id: 3" in text
    assert "kind: transaction_list" in text
    assert "transactions[2]{id,description,amount}:" in text
    assert "1,COFFEE,-4.50" in text
    assert '"SHOP, MAIN"' in text


def test_encode_toon_primitive_list_and_metadata() -> None:
    text = encode_toon({"values": ["Dining", "Groceries", "Car"], "count": 3})
    assert "values[3]: Dining,Groceries,Car" in text
    assert "count: 3" in text


def test_encode_toon_empty_list_and_decimal() -> None:
    text = encode_toon({"groups": [], "spend": Decimal("12.50"), "active": True})
    assert "groups[0]:" in text
    assert "spend: 12.50" in text
    assert "active: true" in text
