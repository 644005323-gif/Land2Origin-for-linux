"""External Origin renderer for normalized electrochemistry series data.

The Raspberry Pi owns parsing and writes a CSV with four required columns:
``series,role,x,y``.  ``role`` is ``curve``/``capacity`` for the left layer,
or ``efficiency`` for the right layer of dual-Y performance plots.  This
script executes only in Windows CPython through OriginExt; it never depends on
Origin's embedded Python console.
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections import OrderedDict
from pathlib import Path

import originpro as op


JOB_FILE = Path(os.environ.get(
    "NDAX_CYCLE_JOB_FILE", r"Z:\home\origin\share\ndax_cycle_plot_job.json"
))
COLORS = ("#A62C2B", "#1F4E79", "#2E8B57", "#D97706", "#7C3AED", "#0891B2")


def _trace(stage: str) -> None:
    trace = Path(os.environ.get(
        "NDAX_ORIGIN_TRACE_FILE", r"Z:\home\origin\share\echem_origin_external_trace.log"
    ))
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(stage + "\n")


def _read_series(path: Path):
    groups = OrderedDict()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = row["series"].strip()
            role = row.get("role", "curve").strip() or "curve"
            x, y = float(row["x"]), float(row["y"])
            groups.setdefault((name, role), ([], []))
            groups[(name, role)][0].append(x)
            groups[(name, role)][1].append(y)
    if not groups:
        raise ValueError("series.csv has no usable points")
    return groups


def _axis_style(layer, *, x_title: str, y_title: str, x_min: float, x_max: float,
                y_min: float, y_max: float, x_step: float | None = None,
                y_step: float | None = None) -> None:
    layer.axis("x").title = x_title
    layer.axis("y").title = y_title
    layer.axis("x").set_limits(x_min, x_max, x_step or _nice_step(x_max - x_min))
    layer.axis("y").set_limits(y_min, y_max, y_step or _nice_step(y_max - y_min))
    commands = [
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
    ]
    layer.lt_exec("\n".join(commands))


def _nice_step(span: float) -> float:
    """Return a positive major-tick step near five visible intervals."""
    target = max(span / 5.0, 1e-9)
    scale = 10 ** math.floor(math.log10(target))
    for multiplier in (1, 2, 5, 10):
        step = multiplier * scale
        if step >= target:
            return step
    return 10 * scale


def _series_key(name: str) -> str:
    """Pair charge/discharge and capacity/efficiency under one visual color."""
    for suffix in (" charge", " discharge", " CE", " E_C"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _titles(plot_type: str):
    if plot_type == "dqdv":
        return (r"\b(Voltage (V vs. Na+/Na))", r"\b(dQ/dV (mAh g\+(-1) V\+(-1)))")
    if plot_type == "cv":
        return (r"\b(Potential (V))", r"\b(Current (mA))")
    if plot_type in {"rate_capability", "cycle_performance_dual_y", "cycle_performance_summary"}:
        return (r"\b(Cycle number)", r"\b(Specific Capacity (mAh g\+(-1)))")
    return (r"\b(Specific Capacity (mAh g\+(-1)))", r"\b(Voltage (V vs. Na+/Na))")


def main() -> None:
    job = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    plot_type = job["plot_type"]
    groups = _read_series(Path(job["series_file"]))
    _trace(f"{plot_type}: start")
    op.new()
    sheet = op.new_sheet(lname=f"{plot_type}_data")
    graph = op.new_graph(lname=plot_type)
    left = graph[0]
    has_right = any(role == "efficiency" for _, role in groups)
    right = graph.add_layer(2) if has_right else None
    _trace(f"{plot_type}: project-ready")

    values_x = [x for xs, _ in groups.values() for x in xs]
    values_y = [y for (name, role), (_, ys) in groups.items() if role != "efficiency" for y in ys]
    col = 0
    left_plot_count = 0
    color_by_key = {}
    left_legend = []
    for index, ((name, role), (xs, ys)) in enumerate(groups.items()):
        sheet.from_list(col, xs, lname=f"{name}_x", axis="X")
        sheet.from_list(col + 1, ys, lname=name, axis="Y")
        target = right if role == "efficiency" and right is not None else left
        curve = target.add_plot(sheet, col + 1, col, "l")
        key = _series_key(name)
        color_by_key.setdefault(key, COLORS[len(color_by_key) % len(COLORS)])
        curve.color = color_by_key[key]
        curve.set_cmd("-w 1000")
        if role == "efficiency":
            curve.symbol = "o"
            curve.symbol_kind = 2
            curve.symbol_interior = 2
        else:
            left_plot_count += 1
            if key not in {entry[0] for entry in left_legend}:
                left_legend.append((key, col // 2 + 1))
        col += 2
    _trace(f"{plot_type}: plots-ready")
    left.rescale()
    xmin, xmax = min(values_x), max(values_x)
    ymin, ymax = min(values_y), max(values_y)
    xpad = max((xmax - xmin) * 0.04, 1.0)
    ypad = max((ymax - ymin) * 0.08, 0.1)
    xtitle, ytitle = _titles(plot_type)
    x_from = 0.0 if plot_type in {"rate_capability", "cycle_performance_dual_y", "cycle_performance_summary"} else xmin - xpad
    _axis_style(left, x_title=xtitle, y_title=ytitle,
                x_min=x_from, x_max=xmax + xpad, y_min=ymin - ypad, y_max=ymax + ypad)
    if has_right and right is not None:
        right.axis("y2").title = r"\b(Coulombic Efficiency (%))"
        right.lt_exec("\n".join((
            "layer.x.label.show = 0;", "layer.x.title.show = 0;",
            "layer.y.label.show = 0;", "layer.y.title.show = 0;",
            "layer.y2.label.show = 1;", "layer.y2.title.show = 1;",
            "layer.y2.thickness = 2;", 'layer.y2.title.font = font("Times New Roman Bold");',
            'layer.y2.label.font = font("Times New Roman Bold");',
            "layer.y2.label.bold = 1;", "layer.y2.label.size = 16;",
            f"layer.x.from = {x_from};", f"layer.x.to = {xmax + xpad};",
            "layer.y.from = 0;", "layer.y.to = 105;", "layer.y.inc = 20;",
        )))
    # A single explicit legend avoids Origin's auto-generated duplicate right-axis entries.
    # Origin auto legends use page coordinates inconsistently across templates
    # and can export outside the PNG frame.  Use stable data-coordinate labels
    # instead, as in the qualified first-cycle workflow.
    left.lt_exec('legend.show = 0; legend.text$ = "";')
    if len(left_legend) > 1 and plot_type != "dqdv":
        label_x = x_from + (xmax + xpad - x_from) * 0.58
        label_y = ymax + ypad - (ymax - ymin + 2 * ypad) * 0.08
        label_step = (ymax - ymin + 2 * ypad) * 0.075
        for index, (name, _) in enumerate(left_legend):
            label = left.add_label(name, label_x, label_y - index * label_step)
            label.color = color_by_key[name]
            label.set_int("fsize", 16)
            label.set_int("bold", 1)
            label.set_str("font", "Times New Roman Bold")
    if plot_type == "dqdv":
        left.lt_exec("draw -l -h 0")
    _trace(f"{plot_type}: styling-ready")
    out_opju, out_png = Path(job["output_opju"]), Path(job["output_png"])
    out_opju.parent.mkdir(parents=True, exist_ok=True)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    op.save(str(out_opju))
    graph.save_fig(str(out_png), width=int(job.get("png_width", 2400)))
    if not out_opju.is_file() or out_opju.stat().st_size < 20_000:
        raise RuntimeError("Origin OPJU missing or too small")
    if not out_png.is_file() or out_png.stat().st_size < 10_000:
        raise RuntimeError("Origin PNG missing or too small")
    Path(job["complete_file"]).write_text(json.dumps({
        "status": "ok", "renderer": "external-cpython-originext", "plot_type": plot_type,
        "series": left_plot_count, "outputs": {"opju": str(out_opju), "png": str(out_png)},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _trace(f"{plot_type}: complete")


if __name__ == "__main__":
    main()
