from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = PROJECT_ROOT / "config" / "workflows"
WORKFLOW_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
REQUIRED_KEYS = {
    "workflow_id", "version", "label", "domain", "enabled", "runner",
    "status", "input_contract", "required_parameters", "delivery_grade",
}


class CatalogError(ValueError):
    pass


def validate_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogError("workflow manifest must be a JSON object")
    missing = REQUIRED_KEYS - set(value)
    if missing:
        raise CatalogError("missing manifest keys: " + ", ".join(sorted(missing)))
    workflow_id = value.get("workflow_id")
    if not isinstance(workflow_id, str) or not WORKFLOW_ID_RE.fullmatch(workflow_id):
        raise CatalogError("invalid workflow_id")
    if not isinstance(value.get("enabled"), bool):
        raise CatalogError("enabled must be boolean")
    for key in ("version", "label", "domain", "runner", "status", "delivery_grade"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise CatalogError(f"{key} must be a non-empty string")
    if not isinstance(value.get("required_parameters"), list) or any(
        not isinstance(item, str) for item in value["required_parameters"]
    ):
        raise CatalogError("required_parameters must be a string array")
    if not isinstance(value.get("input_contract"), dict):
        raise CatalogError("input_contract must be an object")
    return dict(value)


class WorkflowCatalog:
    def __init__(self, root: str | Path = DEFAULT_CATALOG):
        self.root = Path(root)

    def _path(self, workflow_id: str) -> Path:
        if not WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise CatalogError("invalid workflow_id")
        return self.root / f"{workflow_id}.json"

    def list(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.root.glob("*.json")):
            try:
                manifest = validate_manifest(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, CatalogError) as exc:
                raise CatalogError(f"invalid catalog entry {path.name}: {exc}") from exc
            if include_disabled or manifest["enabled"]:
                result.append(manifest)
        return result

    def get(self, workflow_id: str, *, require_enabled: bool = False) -> dict[str, Any]:
        path = self._path(workflow_id)
        try:
            manifest = validate_manifest(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise CatalogError(f"unknown workflow: {workflow_id}") from exc
        except (OSError, json.JSONDecodeError, CatalogError) as exc:
            raise CatalogError(f"invalid workflow {workflow_id}: {exc}") from exc
        if manifest["workflow_id"] != workflow_id:
            raise CatalogError("manifest filename and workflow_id differ")
        if require_enabled and not manifest["enabled"]:
            raise CatalogError(f"workflow is disabled: {workflow_id}")
        return manifest

    def put(self, manifest: object, *, replace: bool = False) -> dict[str, Any]:
        value = validate_manifest(manifest)
        path = self._path(value["workflow_id"])
        if path.exists() and not replace:
            raise CatalogError(f"workflow already exists: {value['workflow_id']}")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return value

    def set_enabled(self, workflow_id: str, enabled: bool) -> dict[str, Any]:
        value = self.get(workflow_id)
        value["enabled"] = enabled
        return self.put(value, replace=True)

    def archive(self, workflow_id: str) -> Path:
        path = self._path(workflow_id)
        self.get(workflow_id)
        archive = self.root / ".archive"
        archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = archive / f"{workflow_id}.{stamp}.json"
        path.replace(destination)
        return destination
