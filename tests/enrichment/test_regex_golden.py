import json
from pathlib import Path

from app.domain.email_source import email_message_from_dict
from app.domain.receipts import RegexReceiptExtractor, receipt_extraction_dump


def test_regex_extractor_golden_cases() -> None:
    base = Path(__file__).with_name("golden")
    extractor = RegexReceiptExtractor()
    for path in sorted(base.glob("*.json")):
        payload = json.loads(path.read_text())
        message = email_message_from_dict(payload["message"])
        extraction = extractor.extract(message, payload.get("known_categories", []))
        dumped = receipt_extraction_dump(extraction)
        expected = payload["expected"]
        for key, value in expected.items():
            assert dumped.get(key) == value, f"{path.name} key={key}"
