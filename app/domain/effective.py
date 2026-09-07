"""Python-side effective labels. SQL counterparts live on app.models."""


def resolved_value(*values: str | None) -> str | None:
    """First non-empty candidate, stripped. None if every candidate is blank."""
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None
