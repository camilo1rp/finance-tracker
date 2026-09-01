from io import StringIO

from app.domain.sources import CsvSource


def test_csv_source_reads_file_like() -> None:
    csv = StringIO("Date,Description,Amount\n2024-03-01,Coffee,4.50\n")
    rows = CsvSource().fetch(file_path=csv)
    assert rows == [
        {"Date": "2024-03-01", "Description": "Coffee", "Amount": 4.5}
    ]


def test_csv_source_turns_empty_cells_into_none() -> None:
    csv = StringIO("Date,Description,Amount,Category\n2024-03-01,Coffee,4.50,\n")
    rows = CsvSource().fetch(file_path=csv)
    assert rows[0]["Category"] is None


def test_csv_source_requires_file_path() -> None:
    try:
        CsvSource().fetch()
    except ValueError as exc:
        assert "file_path" in str(exc)
    else:
        raise AssertionError("expected ValueError")
