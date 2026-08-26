import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from land2origin.workflows.electrochem.ndax_parser import parse_ndax_v14_archive
from land2origin.workflows.electrochem.series import (
    multi_cycle_rows,
    performance_rows,
    rate_rows,
    validate_normalized_series,
    write_battery_curves,
    write_series,
)


SAMPLE = Path("/home/sd1/land2origin_pi_validation_20260823/input/sample_0.2C.ndax")
VALIDATION = Path("/home/sd1/Desktop/Origin-VM-Share/jobs/external_echem_validation_20260827")


def main() -> None:
    archive = parse_ndax_v14_archive(SAMPLE)
    battery = archive.to_battery_data("0.2C")
    assert archive.active_mass_g > 0
    assert battery.cycles
    assert battery.cycles[0].charge_capacity and battery.cycles[0].discharge_capacity

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        battery_csv = write_battery_curves(root / "battery_curves.csv", battery)
        with battery_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert {row["phase"] for row in rows} == {"charge", "discharge"}

        cycles = [cycle.cycle_number for cycle in battery.cycles[:3]]
        multi = write_series(root / "multi.csv", multi_cycle_rows(battery, cycles))
        assert validate_normalized_series(multi, "multi_cycle_charge_discharge") == multi
        dual = write_series(root / "dual.csv", performance_rows(battery, cycles, "Sample-A"))
        assert validate_normalized_series(dual, "cycle_performance_dual_y") == dual
        rate = write_series(root / "rate.csv", rate_rows(
            battery, [[1, min(3, len(battery.cycles)), "0.2C"]], "Sample-A"
        ))
        assert validate_normalized_series(rate, "rate_capability") == rate

    assert validate_normalized_series(VALIDATION / "cv.csv", "cv").is_file()
    assert validate_normalized_series(
        VALIDATION / "cycle_performance_summary.csv", "cycle_performance_summary"
    ).is_file()
    print("[PASS] NDAX and normalized series contracts")


if __name__ == "__main__":
    main()
