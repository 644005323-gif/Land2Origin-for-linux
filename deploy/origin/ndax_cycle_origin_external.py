"""Wait for Origin's Automation server, then run the shared NDAX renderer.

This is deliberately a small wrapper.  The renderer remains identical for
embedded and external Python; OriginExt supplies the external COM connection.
"""
from __future__ import annotations

import time
import os
import json
from pathlib import Path

import originpro as op


RENDERER = Path(r"Z:\home\origin\share\ndax_cycle_origin_guest.py")
RENDERERS = {
    "external_multi_cycle_charge_discharge": Path(r"Z:\home\origin\share\origin_external_multi_cycle_charge_discharge.py"),
    "external_rate_capability": Path(r"Z:\home\origin\share\origin_external_rate_capability.py"),
    "external_dqdv": Path(r"Z:\home\origin\share\origin_external_dqdv.py"),
    "external_cycle_performance_dual_y": Path(r"Z:\home\origin\share\origin_external_cycle_performance_dual_y.py"),
    "external_cycle_performance_summary": Path(r"Z:\home\origin\share\origin_external_cycle_performance_summary.py"),
    "external_cv": Path(r"Z:\home\origin\share\origin_external_cv.py"),
    # Compatibility key used only by the current validation fixtures.
    "echem_external": Path(r"Z:\home\origin\share\echem_origin_external.py"),
}


def main() -> None:
    deadline = time.monotonic() + 180
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            op.attach()
            op.lt_int("@V")
            # The shared renderer runs in this same external process.  A
            # second attach is unnecessary and has been unstable under Wine.
            os.environ["NDAX_ORIGIN_ATTACHED"] = "1"
            break
        except Exception as exc:  # Origin may still be registering its COM server.
            last_error = exc
            time.sleep(1)
    else:
        raise RuntimeError("Origin Automation server was not ready") from last_error

    job_file = Path(os.environ.get(
        "NDAX_CYCLE_JOB_FILE", r"Z:\home\origin\share\ndax_cycle_plot_job.json"
    ))
    job = json.loads(job_file.read_text(encoding="utf-8"))
    renderer = RENDERERS.get(job.get("renderer"), RENDERER)
    source = renderer.read_text(encoding="utf-8")
    exec(compile(source, str(renderer), "exec"), {"__name__": "__main__"})
    # OriginExt's normal CPython finalization can fault under Wine/Box64 after
    # the OPJU, PNG, and completion manifest have all been written.  The
    # external benchmark uses the same direct process exit successfully.
    os._exit(0)


if __name__ == "__main__":
    main()
