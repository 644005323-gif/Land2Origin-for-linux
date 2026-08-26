"""Origin guest-side renderer for NDAX battery curves.

The Raspberry Pi host writes ``ndax_cycle_plot_job.json`` and the selected
``battery_curves.csv`` into the Origin VM share.  This file is executed inside
Origin's Python console by the same headless launcher used by the XRD Rietveld
workflow.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

import originpro as op


JOB_FILE = Path(os.environ.get(
    "NDAX_CYCLE_JOB_FILE",
    r"Z:\home\origin\share\ndax_cycle_plot_job.json",
))
TRACE_STARTED = time.perf_counter()


def _trace(stage: str) -> None:
    """Write a phase marker outside Origin so a Wine crash remains diagnosable."""
    log_file = Path(os.environ.get(
        "NDAX_ORIGIN_TRACE_FILE", r"Z:\home\origin\share\ndax_origin_external_trace.log"
    ))
    with log_file.open("a", encoding="utf-8") as handle:
        elapsed = time.perf_counter() - TRACE_STARTED
        handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} +{elapsed:.3f}s {stage}\n")


def _occupied_corner(capacities, voltages, x_to, y_from, y_to):
    boxes = {
        "upper_left": (0.06, 0.72, 0.42, 0.94, (0.06, 0.93)),
        "upper_right": (0.58, 0.72, 0.94, 0.94, (0.58, 0.93)),
        "lower_left": (0.06, 0.06, 0.42, 0.28, (0.06, 0.27)),
        "lower_right": (0.58, 0.06, 0.94, 0.28, (0.58, 0.27)),
    }
    counts = {}
    for name, (left, bottom, right, top, _) in boxes.items():
        counts[name] = sum(
            left <= x / x_to <= right and bottom <= (y - y_from) / (y_to - y_from) <= top
            for x, y in zip(capacities, voltages)
        )
    order = ("upper_left", "upper_right", "lower_left", "lower_right")
    selected = min(order, key=lambda name: counts[name])
    return selected, boxes[selected][4], counts


def _render_first_cycle(job, rows):
    _trace("first-cycle: start")
    branches = {}
    for phase in ("charge", "discharge"):
        if 1 not in rows or phase not in rows[1]:
            continue
        branch = rows[1][phase]
        baseline = min(branch["x"])
        branches[phase] = ([value - baseline for value in branch["x"]], branch["y"])
    if set(branches) != {"charge", "discharge"}:
        raise ValueError("首圈任务必须同时包含 charge 和 discharge 数据")

    capacities = [value for x, _ in branches.values() for value in x]
    voltages = [value for _, y in branches.values() for value in y]
    x_max = max(capacities)
    y_min, y_max = min(voltages), max(voltages)
    x_to = max(20.0, math.ceil(x_max * 1.05 / 20.0) * 20.0)
    y_from = math.floor((y_min - 0.08) * 10.0) / 10.0
    y_to = math.ceil((y_max + 0.08) * 10.0) / 10.0
    corner, anchor, counts = _occupied_corner(capacities, voltages, x_to, y_from, y_to)
    legend_x = anchor[0] * x_to
    legend_y = y_from + anchor[1] * (y_to - y_from)
    legend_step = (y_to - y_from) * 0.065

    if os.environ.get("NDAX_ORIGIN_ATTACHED") != "1":
        _trace("first-cycle: attach")
        op.attach()
    _trace("first-cycle: new-project")
    op.new()
    _trace("first-cycle: new-sheet")
    sheet = op.new_sheet(lname="NDAX_first_cycle_data")
    # Match the API sequence proven by origin_external_benchmark.py.  The
    # explicit line template is not needed and is less stable under Wine.
    _trace("first-cycle: new-graph")
    graph = op.new_graph(lname="NDAX_first_cycle")
    layer = graph[0]
    colors = {"charge": "#A62C2B", "discharge": "#1F4E79"}
    column = 0
    for phase in ("charge", "discharge"):
        x_values, y_values = branches[phase]
        sheet.from_list(column, x_values, lname=f"1st {phase} capacity")
        sheet.from_list(column + 1, y_values, lname=f"1st {phase} voltage")
        sheet.cols_axis("xy", c1=column, c2=column + 1)
        curve = layer.add_plot(sheet, coly=column + 1, colx=column, type="l")
        curve.color = colors[phase]
        curve.set_cmd("-w 1000")
        column += 2

    _trace("first-cycle: data-and-plots-complete")

    # The baseline benchmark rescales before any export.  Without this call
    # Origin retains its default 0--10 axes and clips real capacity data.
    _trace("first-cycle: rescale")
    layer.rescale()

    # This profile deliberately stops at known-good benchmark API calls.  It
    # is only used for controlled diagnosis with real data; formal jobs keep
    # the publication formatting below.
    baseline_only = job.get("render_profile") == "baseline"
    if baseline_only:
        _trace("first-cycle: baseline-save-opju")
        output_opju = Path(job["output_opju"])
        output_png = Path(job["output_png"])
        output_opju.parent.mkdir(parents=True, exist_ok=True)
        output_png.parent.mkdir(parents=True, exist_ok=True)
        op.save(str(output_opju))
        _trace("first-cycle: baseline-export-png")
        graph.save_fig(str(output_png), width=2400)
        _trace("first-cycle: baseline-complete")
        _write_first_cycle_complete(job, branches, output_opju, output_png,
                                    x_to, y_from, y_to, corner, counts,
                                    profile="baseline")
        return

    _trace("first-cycle: format-axes")
    layer.axis("x").title = r"\b(Specific Capacity (mAh g\+(-1)))"
    layer.axis("y").title = r"\b(Voltage (V vs. Na+/Na))"
    layer.axis("x").set_limits(0.0, x_to, 20.0)
    layer.axis("y").set_limits(y_from, y_to, 0.5)
    format_commands = (
        "layer.x.showAxes = 3;", "layer.y.showAxes = 3;",
        "layer.x.minorTicks = 1;", "layer.y.minorTicks = 1;",
        "layer.x.ticks = 5;", "layer.y.ticks = 5;",
        "layer.x2.ticks = 0;", "layer.y2.ticks = 0;",
        "layer.x2.label.show = 0;", "layer.y2.label.show = 0;",
        "layer.x.thickness = 2;", "layer.y.thickness = 2;",
        "layer.x2.thickness = 2;", "layer.y2.thickness = 2;",
        'layer.font = font("Times New Roman Bold");',
        'layer.x.title.font = font("Times New Roman Bold");',
        'layer.y.title.font = font("Times New Roman Bold");',
        'layer.x.label.font = font("Times New Roman Bold");',
        'layer.y.label.font = font("Times New Roman Bold");',
        "layer.x.title.fSize = 18;", "layer.y.title.fSize = 18;",
        "layer.x.label.size = 16;", "layer.y.label.size = 16;",
        "layer.x.label.bold = 1;", "layer.y.label.bold = 1;",
        "axis -ps X BOLD 1;", "axis -ps Y BOLD 1;",
        "legend.show = 0;", "legend.text$ = \"\";",
    )
    # Each lt_exec crosses the external OriginExt COM boundary. The commands
    # are independent LabTalk statements, so one call avoids 29 round trips.
    _trace("first-cycle: format-commands-batched")
    layer.lt_exec("\n".join(format_commands))
    for index, phase in enumerate(("charge", "discharge")):
        _trace(f"first-cycle: add-label-{phase}")
        label = layer.add_label(phase.title(), legend_x, legend_y - index * legend_step)
        label.color = colors[phase]
        label.set_int("fsize", 16)
        label.set_int("bold", 1)
        label.set_str("font", "Times New Roman Bold")

    _trace("first-cycle: save-opju")
    output_opju = Path(job["output_opju"])
    output_png = Path(job["output_png"])
    output_opju.parent.mkdir(parents=True, exist_ok=True)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    op.save(str(output_opju))
    _trace("first-cycle: export-png")
    graph.save_fig(str(output_png), width=2400)
    _trace("first-cycle: validate-outputs")
    if not output_opju.is_file() or output_opju.stat().st_size < 20000:
        raise ValueError("Origin OPJU 缺失或过小")
    if not output_png.is_file() or output_png.stat().st_size < 10000:
        raise ValueError("Origin PNG 缺失或过小")
    _write_first_cycle_complete(job, branches, output_opju, output_png,
                                x_to, y_from, y_to, corner, counts,
                                profile="formal")


def _write_first_cycle_complete(job, branches, output_opju, output_png,
                                x_to, y_from, y_to, corner, counts, profile):
    if not output_opju.is_file() or output_opju.stat().st_size < 20000:
        raise ValueError("Origin OPJU 缺失或过小")
    if not output_png.is_file() or output_png.stat().st_size < 10000:
        raise ValueError("Origin PNG 缺失或过小")
    _trace(f"first-cycle: write-complete ({profile})")
    Path(job["complete_file"]).write_text(json.dumps({
        "status": "ok", "workflow": "origin_first_cycle_charge_discharge_v1",
        "plot_type": "充放电曲线", "renderer": "Origin OPJU -> Origin PNG",
        "rate_label": job.get("rate_label"), "cycle_numbers": [1],
        "points": {phase: len(values[0]) for phase, values in branches.items()},
        "outputs": {"opju": str(output_opju), "png": str(output_png)},
        "origin_layout": {"axis_limits": {"x": [0.0, x_to], "y": [y_from, y_to]},
                           "legend": {"corner": corner, "corner_point_counts": counts}},
        "render_profile": profile,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    job = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    rows = defaultdict(lambda: defaultdict(lambda: {"x": [], "y": []}))
    requested = sorted({int(value) for value in job.get("selected_cycles", [])})
    selected = set(requested)
    with Path(job["csv_file"]).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            cycle = int(row["cycle_number"])
            if cycle not in selected:
                continue
            phase = row.get("phase", "curve")
            rows[cycle][phase]["x"].append(float(row["capacity_mAh_g"]))
            rows[cycle][phase]["y"].append(float(row["voltage_V"]))
    if not rows:
        raise ValueError("No selected NDAX cycles in battery_curves.csv")

    # New production path. The existing multi-cycle renderer below remains for
    # historical compatibility, but the stable first-cycle job is Origin-only
    # with controlled in-layer labels and the approved delivery manifest.
    if job.get("plot_type") == "first_cycle_charge_discharge" or sorted(rows) == [1]:
        _render_first_cycle(job, rows)
        return

    # A high-rate NDAX may contain hundreds of cycles and tens of thousands of
    # points. Keep the conversion tables complete, but bound the editable
    # Origin graph to six representative cycles and 500 points per branch.
    cycle_ids = sorted(rows)
    if len(cycle_ids) > 6:
        keep = {cycle_ids[0], cycle_ids[-1], cycle_ids[-2],
                cycle_ids[len(cycle_ids) // 4], cycle_ids[len(cycle_ids) // 2],
                cycle_ids[(3 * len(cycle_ids)) // 4]}
        rows = {cycle: rows[cycle] for cycle in cycle_ids if cycle in keep}
    max_points = int(job.get("max_points_per_branch", 500))
    for cycle in rows:
        for phase in rows[cycle]:
            branch = rows[cycle][phase]
            if len(branch["x"]) <= max_points:
                continue
            stride = (len(branch["x"]) - 1) / (max_points - 1)
            indices = sorted({round(index * stride) for index in range(max_points)})
            branch["x"] = [branch["x"][index] for index in indices]
            branch["y"] = [branch["y"][index] for index in indices]

    op.attach()
    op.new()
    sheet = op.new_sheet(lname="NDAX_source_data")
    graph = op.new_graph(lname="NDAX_cycle_curve")
    layer = graph[0]
    colors = ("#DC143C", "#1E90FF", "#32CD32", "#FF8C00", "#9370DB", "#FF1493")
    legend_entries = []
    column = 0
    for index, cycle in enumerate(sorted(rows)):
        for phase in ("charge", "discharge", "curve"):
            if phase not in rows[cycle]:
                continue
            x_values, y_values = rows[cycle][phase]["x"], rows[cycle][phase]["y"]
            sheet.from_list(column, x_values, lname=f"{cycle} {phase} capacity")
            sheet.from_list(column + 1, y_values, lname=f"{cycle} {phase} voltage")
            sheet.cols_axis("xy", c1=column, c2=column + 1)
            curve = layer.add_plot(sheet, coly=column + 1, colx=column, type="l")
            curve.color = colors[index % len(colors)]
            curve.set_cmd("-w 1000")
            column += 2
        ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(cycle, f"{cycle}th")
        # Use the first branch of each cycle as its single legend sample.
        legend_entries.append(rf"\L({index * 2 + 1}, style:l) {ordinal}")

    layer.axis("x").title = r"\b(Specific Capacity (mAh/g))"
    layer.axis("y").title = r"\b(Voltage (V vs. Na+/Na))"
    layer.set_xlim(begin=0, end=float(job.get("x_axis_max", 100)), step=20)
    layer.set_ylim(begin=float(job.get("y_axis_min", 1.5)), end=float(job.get("y_axis_max", 4.5)), step=0.5)
    for command in (
        "layer.x.showAxes = 3;", "layer.y.showAxes = 3;",
        "layer.x.minorTicks = 1;", "layer.y.minorTicks = 1;",
        "layer.x.ticks = 5;", "layer.y.ticks = 5;",
        "layer.x2.ticks = 0;", "layer.y2.ticks = 0;",
        "layer.x.thickness = 2;", "layer.y.thickness = 2;",
        "layer.x2.thickness = 2;", "layer.y2.thickness = 2;",
        'layer.font = font("Times New Roman Bold");',
        'layer.x.title.font = font("Times New Roman Bold");',
        'layer.y.title.font = font("Times New Roman Bold");',
        'layer.x.label.font = font("Times New Roman Bold");',
        'layer.y.label.font = font("Times New Roman Bold");',
        "layer.x.title.fSize = 16;", "layer.y.title.fSize = 16;",
        "layer.x.label.fSize = 14;", "layer.y.label.fSize = 14;",
        "layer.x.title.font.bold = 1;", "layer.y.title.font.bold = 1;",
        "layer.x.label.font.bold = 1;", "layer.y.label.font.bold = 1;",
    ):
        layer.lt_exec(command)
    layer.lt_exec("legend -r;")
    layer.lt_exec('legend.text$="' + "  ".join(legend_entries) + '";')
    for command in (
        "legend.update = 0;", "legend.show = 1;", "legend.border = 0;",
        'legend.font.name$ = "Times New Roman";', "legend.font.size = 16;",
        "legend.font.bold = 1;", "legend.background = 0;",
        "legend.x = layer.x.to - legend.dx / 2;",
        "legend.y = layer.y.to - legend.dy / 2;",
    ):
        layer.lt_exec(command)

    op.save(str(job["output_opju"]))
    graph.save_fig(str(job["output_png"]), width=1800)
    Path(job["complete_file"]).write_text(json.dumps({
        "status": "ok", "opju": job["output_opju"], "png": job["output_png"]
    }), encoding="utf-8")


if __name__ == "__main__":
    main()
