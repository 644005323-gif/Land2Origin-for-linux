import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from land2origin.integration import ELECTROCHEM_WORKFLOWS, codex_handoff


def main() -> None:
    for workflow_id in ELECTROCHEM_WORKFLOWS:
        prompt = codex_handoff(workflow_id)
        assert f"workflow_id: {workflow_id}" in prompt
        assert "explicitly\n   confirms" in prompt
        assert "land2origin.cli execute" in prompt
        assert '"type":"image"' in prompt
        assert '"type":"file"' in prompt
    try:
        codex_handoff("xrd_rietveld")
    except ValueError:
        pass
    else:
        raise AssertionError("XRD entered the electrochem adapter")
    print("[PASS] controlled Codex channel handoff")


if __name__ == "__main__":
    main()
