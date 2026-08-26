import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from land2origin.catalog import CatalogError, WorkflowCatalog


def main() -> None:
    production = WorkflowCatalog()
    entries = production.list()
    assert {item["workflow_id"] for item in entries} >= {
        "first_cycle_charge_discharge", "rate_capability", "cv", "xrd_rietveld"
    }
    assert production.get("ftir")["enabled"] is False

    with tempfile.TemporaryDirectory() as temp:
        catalog = WorkflowCatalog(temp)
        manifest = dict(production.get("cv"))
        manifest["workflow_id"] = "test_cv"
        catalog.put(manifest)
        assert catalog.get("test_cv")["enabled"] is True
        catalog.set_enabled("test_cv", False)
        assert catalog.get("test_cv")["enabled"] is False
        manifest["label"] = "Updated"
        catalog.put(manifest, replace=True)
        assert catalog.get("test_cv")["label"] == "Updated"
        archived = catalog.archive("test_cv")
        assert archived.is_file()
        try:
            catalog.get("test_cv")
        except CatalogError:
            pass
        else:
            raise AssertionError("archived workflow remained active")
        json.loads(archived.read_text(encoding="utf-8"))
    print("[PASS] workflow catalog CRUD")


if __name__ == "__main__":
    main()
