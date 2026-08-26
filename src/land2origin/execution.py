from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from .catalog import WorkflowCatalog
from .contracts import JobError, WorkflowJob
from .workflows.electrochem.ndax_parser import parse_ndax_v14_archive
from .workflows.electrochem.series import (
    dqdv_rows,
    multi_cycle_rows,
    performance_rows,
    rate_rows,
    validate_normalized_series,
    write_battery_curves,
    write_series,
)


SHARE_ROOT = Path(os.environ.get("LAND2ORIGIN_SHARE_ROOT", "/home/sd1/Desktop/Origin-VM-Share"))
ARCHIVE_ROOT = Path(os.environ.get(
    "LAND2ORIGIN_ECHEM_ARCHIVE_ROOT",
    "/home/sd1/Nutstore Files/表征数据 - 同步副本/电化学",
))
ORIGIN_RUNNER = Path(os.environ.get("LAND2ORIGIN_ORIGIN_RUNNER", "/home/sd1/.local/bin/ndax-origin-runner"))
ORIGIN_JOB_FILE = SHARE_ROOT / "ndax_cycle_plot_job.json"
ORIGIN_LOCK_FILE = SHARE_ROOT / ".land2origin-origin.lock"
RENDERERS = {
    "multi_cycle_charge_discharge": "external_multi_cycle_charge_discharge",
    "rate_capability": "external_rate_capability",
    "dqdv": "external_dqdv",
    "cycle_performance_dual_y": "external_cycle_performance_dual_y",
    "cycle_performance_summary": "external_cycle_performance_summary",
    "cv": "external_cv",
}
NDAX_WORKFLOWS = {
    "first_cycle_charge_discharge", "multi_cycle_charge_discharge", "rate_capability",
    "dqdv", "cycle_performance_dual_y",
}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip()).strip("._")
    return cleaned[:80] or "sample"


def _windows_share_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(SHARE_ROOT.resolve())
    except ValueError as exc:
        raise JobError(f"Origin input is outside the shared directory: {path}") from exc
    return str(PureWindowsPath("Z:/home/origin/share", *relative.parts))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_parameters(job: WorkflowJob, manifest: dict[str, Any]) -> None:
    missing = [name for name in manifest["required_parameters"]
               if job.parameters.get(name) in (None, "", [])]
    if missing:
        raise JobError("missing workflow parameters: " + ", ".join(missing))


def _cycles(value: object, *, available: int) -> list[int]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in value
    ):
        raise JobError("cycle_numbers must be a non-empty positive integer array")
    result = list(dict.fromkeys(value))
    if max(result) > available:
        raise JobError(f"requested cycle {max(result)} exceeds available cycle count {available}")
    return result


def _prepare(job: WorkflowJob, staging: Path, workflow_id: str) -> tuple[Path, dict[str, Any]]:
    parameters = job.parameters
    sample = str(parameters.get("sample_name") or "")
    if workflow_id in NDAX_WORKFLOWS:
        if len(job.sources) != 1 or job.sources[0].suffix.casefold() != ".ndax":
            raise JobError(f"{workflow_id} requires exactly one .ndax source")
        rate = str(parameters.get("rate_label") or "1C")
        archive = parse_ndax_v14_archive(job.sources[0])
        battery = archive.to_battery_data(rate)
        audit = {
            "source_format": "neware_ndax_v14",
            "active_mass_g": archive.active_mass_g,
            "available_cycles": len(battery.cycles),
        }
        battery_csv = write_battery_curves(staging / "battery_curves.csv", battery)
        if workflow_id == "first_cycle_charge_discharge":
            return battery_csv, {**audit, "selected_cycles": [1]}
        if workflow_id == "rate_capability":
            series = write_series(
                staging / "series.csv",
                rate_rows(battery, parameters["rate_schedule"], sample),
            )
            return series, audit
        cycles = _cycles(parameters.get("cycle_numbers"), available=len(battery.cycles))
        if workflow_id == "multi_cycle_charge_discharge":
            rows = multi_cycle_rows(battery, cycles)
        elif workflow_id == "dqdv":
            rows = dqdv_rows(battery, cycles, int(parameters.get("smooth_window", 11)))
        else:
            rows = performance_rows(battery, cycles, sample)
        return write_series(staging / "series.csv", rows), {**audit, "selected_cycles": cycles}

    if workflow_id not in {"cv", "cycle_performance_summary"}:
        raise JobError(f"no electrochem preparer for {workflow_id}")
    if len(job.sources) != 1:
        raise JobError(f"{workflow_id} currently requires one normalized series CSV")
    source = validate_normalized_series(job.sources[0], workflow_id)
    destination = staging / "series.csv"
    shutil.copy2(source, destination)
    return destination, {"source_format": "normalized_series_v1"}


def _copy_sources(sources: tuple[Path, ...], destination: Path) -> list[dict[str, str]]:
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    used: set[str] = set()
    for index, source in enumerate(sources, 1):
        name = source.name
        if name in used:
            name = f"{index}_{name}"
        used.add(name)
        target = destination / name
        shutil.copy2(source, target)
        records.append({"original_path": str(source), "archived_path": str(target), "sha256": _sha256(target)})
    return records


def _origin_job(workflow_id: str, run_id: str, prepared: Path, staging: Path,
                parameters: dict[str, Any]) -> dict[str, Any]:
    base = _safe_name(str(parameters.get("sample_name") or run_id)) + "_" + workflow_id
    value: dict[str, Any] = {
        "run_id": run_id,
        "plot_type": workflow_id,
        "output_opju": _windows_share_path(staging / f"{base}.opju"),
        "output_png": _windows_share_path(staging / f"{base}.png"),
        "complete_file": _windows_share_path(staging / "origin_complete.json"),
    }
    if workflow_id == "first_cycle_charge_discharge":
        value.update({
            "rate_label": parameters["rate_label"],
            "cycle_numbers": [1],
            "selected_cycles": [1],
            "csv_file": _windows_share_path(prepared),
        })
    else:
        value.update({"renderer": RENDERERS[workflow_id], "series_file": _windows_share_path(prepared)})
    return value


def execute(job: WorkflowJob, *, catalog: WorkflowCatalog | None = None,
            timeout: int = 180) -> dict[str, Any]:
    catalog = catalog or WorkflowCatalog()
    manifest = catalog.get(job.workflow_id, require_enabled=True)
    if manifest["runner"] != "external_origin_electrochem":
        raise JobError(f"unsupported runner: {manifest['runner']}")
    if not job.user_confirmed:
        raise JobError("job requires explicit user confirmation")
    _required_parameters(job, manifest)
    if timeout < 30 or timeout > 900:
        raise JobError("timeout must be between 30 and 900 seconds")
    if not ORIGIN_RUNNER.is_file() or not os.access(ORIGIN_RUNNER, os.X_OK):
        raise JobError(f"Origin runner is unavailable: {ORIGIN_RUNNER}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = job.run_id or f"{stamp}_{job.workflow_id}_{uuid.uuid4().hex[:6]}"
    staging = SHARE_ROOT / "jobs" / run_id
    archive = ARCHIVE_ROOT / run_id
    if staging.exists() or archive.exists():
        raise JobError(f"run_id already exists: {run_id}")
    staging.mkdir(parents=True)
    archive.mkdir(parents=True)
    source_records = _copy_sources(job.sources, archive / "源文件")
    outputs_dir = archive / "Origin输出"
    outputs_dir.mkdir()
    started = time.monotonic()
    final: dict[str, Any]
    try:
        prepared, preparation_audit = _prepare(job, staging, job.workflow_id)
        origin_job = _origin_job(job.workflow_id, run_id, prepared, staging, job.parameters)
        origin_complete = staging / "origin_complete.json"
        with ORIGIN_LOCK_FILE.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            temporary = ORIGIN_JOB_FILE.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(origin_job, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(ORIGIN_JOB_FILE)
            completed = subprocess.run(
                [str(ORIGIN_RUNNER), str(origin_complete), str(timeout)],
                capture_output=True, text=True, timeout=timeout + 30,
            )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or
                               f"Origin runner exited {completed.returncode}")
        try:
            origin_result = json.loads(origin_complete.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Origin completion manifest is invalid: {exc}") from exc
        if origin_result.get("status") != "ok":
            raise RuntimeError("Origin completion status is not ok")
        staged_opju = next(staging.glob("*.opju"), None)
        staged_png = next(staging.glob("*.png"), None)
        if not staged_opju or staged_opju.stat().st_size < 20_000:
            raise RuntimeError("Origin OPJU is missing or too small")
        if not staged_png or staged_png.stat().st_size < 10_000:
            raise RuntimeError("Origin PNG is missing or too small")
        archived_opju = outputs_dir / staged_opju.name
        archived_png = outputs_dir / staged_png.name
        shutil.copy2(staged_opju, archived_opju)
        shutil.copy2(staged_png, archived_png)
        final = {
            "status": "ok", "schema_version": "1.0", "run_id": run_id,
            "workflow_id": job.workflow_id, "workflow_version": manifest["version"],
            "delivery_grade": manifest["delivery_grade"],
            "duration_seconds": round(time.monotonic() - started, 3),
            "sources": source_records, "parameters": job.parameters,
            "preparation_audit": preparation_audit,
            "outputs": {"opju": str(archived_opju), "png": str(archived_png)},
            "origin": origin_result,
        }
    except Exception as exc:
        final = {
            "status": "failed", "schema_version": "1.0", "run_id": run_id,
            "workflow_id": job.workflow_id,
            "duration_seconds": round(time.monotonic() - started, 3),
            "sources": source_records, "parameters": job.parameters,
            "error": str(exc), "staging_dir": str(staging),
        }
    (archive / "complete.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    final["archive_dir"] = str(archive)
    return final
