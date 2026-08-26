from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Iterable

from .models import BatteryData


FIELDS = ("series", "role", "x", "y")
ROLES = {"curve", "capacity", "efficiency"}


def write_series(path: str | Path, rows: Iterable[dict]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            role = str(row.get("role") or "")
            if role not in ROLES:
                raise ValueError(f"unsupported series role: {role}")
            x, y = float(row["x"]), float(row["y"])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("series points must be finite")
            writer.writerow({"series": str(row["series"]), "role": role, "x": x, "y": y})
            count += 1
    if not count:
        destination.unlink(missing_ok=True)
        raise ValueError("series has no points")
    return destination


def validate_normalized_series(path: str | Path, workflow_id: str) -> Path:
    source = Path(path)
    roles: set[str] = set()
    count = 0
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or set(FIELDS) - set(reader.fieldnames):
            raise ValueError("normalized series CSV requires series,role,x,y columns")
        for row in reader:
            role = str(row.get("role") or "")
            if role not in ROLES:
                raise ValueError(f"unsupported series role: {role}")
            if not str(row.get("series") or "").strip():
                raise ValueError("series name cannot be empty")
            x, y = float(row["x"]), float(row["y"])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("series points must be finite")
            roles.add(role)
            count += 1
    if count < 2:
        raise ValueError("normalized series CSV needs at least two points")
    if workflow_id in {"cycle_performance_dual_y", "cycle_performance_summary"}:
        if roles != {"capacity", "efficiency"}:
            raise ValueError("dual-Y workflow requires capacity and efficiency roles")
    elif roles != {"curve"}:
        raise ValueError(f"{workflow_id} requires curve role only")
    return source


def write_battery_curves(path: str | Path, data: BatteryData) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = ("cycle_number", "rate_label", "phase", "point_in_phase", "capacity_mAh_g", "voltage_V")
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cycle in data.cycles:
            for phase, capacities, voltages in (
                ("charge", cycle.charge_capacity, cycle.charge_voltage),
                ("discharge", cycle.discharge_capacity, cycle.discharge_voltage),
            ):
                for point, (capacity, voltage) in enumerate(zip(capacities, voltages), 1):
                    writer.writerow({"cycle_number": cycle.cycle_number, "rate_label": cycle.rate_label,
                                     "phase": phase, "point_in_phase": point,
                                     "capacity_mAh_g": capacity, "voltage_V": voltage})
    return destination


def _selected(data: BatteryData, cycles: list[int]) -> list:
    selected = [cycle for cycle in data.cycles if cycle.cycle_number in set(cycles)]
    if not selected:
        raise ValueError("none of the requested cycles exist in the NDAX source")
    return selected


def multi_cycle_rows(data: BatteryData, cycles: list[int]):
    for cycle in _selected(data, cycles):
        ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(cycle.cycle_number, f"{cycle.cycle_number}th")
        for phase, xs, ys in (
            ("charge", cycle.charge_capacity, cycle.charge_voltage),
            ("discharge", cycle.discharge_capacity, cycle.discharge_voltage),
        ):
            baseline = min(xs)
            for x, y in zip(xs, ys):
                yield {"series": f"{ordinal} {phase}", "role": "curve", "x": x - baseline, "y": y}


def performance_rows(data: BatteryData, cycles: list[int], sample: str):
    for cycle in _selected(data, cycles):
        charge = max(cycle.charge_capacity) - min(cycle.charge_capacity)
        discharge = max(cycle.discharge_capacity) - min(cycle.discharge_capacity)
        if charge <= 0:
            continue
        yield {"series": sample, "role": "capacity", "x": cycle.cycle_number, "y": discharge}
        yield {"series": sample + " CE", "role": "efficiency", "x": cycle.cycle_number,
               "y": discharge / charge * 100.0}


def rate_rows(data: BatteryData, schedule: list, sample: str):
    rate_for_cycle: dict[int, str] = {}
    for item in schedule:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError("rate_schedule entries must be [first_cycle,last_cycle,label]")
        first, last, label = int(item[0]), int(item[1]), str(item[2])
        if first < 1 or last < first or not label.strip():
            raise ValueError("invalid rate_schedule entry")
        for cycle in range(first, last + 1):
            if cycle in rate_for_cycle:
                raise ValueError("rate_schedule cycle ranges overlap")
            rate_for_cycle[cycle] = label
    rows = []
    for cycle in data.cycles:
        if cycle.cycle_number not in rate_for_cycle or not cycle.discharge_capacity:
            continue
        capacity = max(cycle.discharge_capacity) - min(cycle.discharge_capacity)
        rows.append({"series": sample, "role": "curve", "x": cycle.cycle_number, "y": capacity})
    if not rows:
        raise ValueError("no NDAX cycles matched the explicit rate_schedule")
    return rows


def dqdv_rows(data: BatteryData, cycles: list[int], smooth_window: int = 11):
    try:
        import numpy as np
        from scipy.signal import savgol_filter
    except ImportError as exc:
        raise RuntimeError("dQ/dV preparation requires the configured NumPy/SciPy runtime") from exc
    for cycle in _selected(data, cycles):
        ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(cycle.cycle_number, f"{cycle.cycle_number}th")
        for phase, capacity, voltage, sign in (
            ("charge", cycle.charge_capacity, cycle.charge_voltage, 1),
            ("discharge", cycle.discharge_capacity, cycle.discharge_voltage, -1),
        ):
            if len(capacity) < 3:
                continue
            cap = np.asarray(capacity, dtype=float)
            volt = np.asarray(voltage, dtype=float)
            cap = cap - cap.min()
            window = min(int(smooth_window), len(volt) if len(volt) % 2 else len(volt) - 1)
            if window >= 5:
                volt = savgol_filter(volt, window if window % 2 else window - 1, 3)
            dv, dq = np.gradient(volt), np.gradient(cap)
            threshold = max(1e-5, float(np.median(np.abs(dv))) * 0.1)
            valid = np.abs(dv) > threshold
            for x, y in zip(volt[valid], (dq[valid] / dv[valid]) * sign):
                yield {"series": f"{ordinal} {phase}", "role": "curve", "x": x, "y": y}
