"""Shared account mapping payloads for tests."""

CARD_TYPE_MAPPING = {
    "date_col": "Date",
    "description_col": "Description",
    "amount_col": "Amount",
    "type_col": "Type",
}

SIGN_ONLY_MAPPING = {
    "date_col": "Date",
    "description_col": "Description",
    "amount_col": "Amount",
    "sign_convention": "negative_is_spend",
}

MINIMAL_MAPPING = {
    "date_col": "Date",
    "description_col": "Description",
    "amount_col": "Amount",
}
