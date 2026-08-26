from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class JobError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowJob:
    workflow_id: str
    sources: tuple[Path, ...]
    parameters: dict[str, Any]
    user_confirmed: bool
    run_id: str | None = None
    schema_version: str = "1.0"

    @classmethod
    def load(cls, path: str | Path) -> "WorkflowJob":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobError(f"cannot read workflow job: {exc}") from exc
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: object) -> "WorkflowJob":
        if not isinstance(value, dict):
            raise JobError("job must be a JSON object")
        allowed = {"schema_version", "workflow_id", "sources", "parameters", "user_confirmed", "run_id"}
        unknown = set(value) - allowed
        if unknown:
            raise JobError("unknown job keys: " + ", ".join(sorted(unknown)))
        workflow_id = value.get("workflow_id")
        sources = value.get("sources")
        parameters = value.get("parameters", {})
        confirmed = value.get("user_confirmed")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise JobError("workflow_id is required")
        if not isinstance(sources, list) or not sources or any(not isinstance(item, str) for item in sources):
            raise JobError("sources must be a non-empty string array")
        paths = tuple(Path(item).expanduser().resolve() for item in sources)
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise JobError("source files do not exist: " + ", ".join(missing))
        if not isinstance(parameters, dict):
            raise JobError("parameters must be an object")
        if not isinstance(confirmed, bool):
            raise JobError("user_confirmed must be boolean")
        run_id = value.get("run_id")
        if run_id is not None and (not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id)):
            raise JobError("run_id contains unsupported characters")
        schema_version = value.get("schema_version", "1.0")
        if schema_version != "1.0":
            raise JobError(f"unsupported schema_version: {schema_version}")
        return cls(workflow_id, paths, dict(parameters), confirmed, run_id, schema_version)
