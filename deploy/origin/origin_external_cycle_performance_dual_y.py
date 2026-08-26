"""External Origin entry point for capacity plus coulombic-efficiency plots."""
from pathlib import Path
import json
import os

EXPECTED = "cycle_performance_dual_y"
JOB = Path(os.environ.get("NDAX_CYCLE_JOB_FILE", r"Z:\home\origin\share\ndax_cycle_plot_job.json"))
if json.loads(JOB.read_text(encoding="utf-8")).get("plot_type") != EXPECTED:
    raise ValueError(f"{EXPECTED} renderer received a different job")
exec(compile(Path(r"Z:\home\origin\share\echem_origin_external.py").read_text(encoding="utf-8"),
             r"Z:\home\origin\share\echem_origin_external.py", "exec"), {"__name__": "__main__"})
