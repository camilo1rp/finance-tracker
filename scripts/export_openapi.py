"""Export FastAPI OpenAPI schema to JSON for code generation."""
import json
from pathlib import Path

from app.main import app


def export_openapi() -> None:
    schema = app.openapi()
    out_path = Path("frontend/openapi.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Exported OpenAPI schema ({len(schema.get('paths', {}))} paths) to {out_path}")


if __name__ == "__main__":
    export_openapi()
