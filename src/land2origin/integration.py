from __future__ import annotations

from typing import Any

from .catalog import WorkflowCatalog


ELECTROCHEM_WORKFLOWS = frozenset({
    "first_cycle_charge_discharge",
    "multi_cycle_charge_discharge",
    "rate_capability",
    "dqdv",
    "cycle_performance_dual_y",
    "cycle_performance_summary",
    "cv",
})


def codex_handoff(workflow_id: str, *, catalog: WorkflowCatalog | None = None) -> str:
    """Build the controlled channel-to-Codex contract for one workflow."""
    catalog = catalog or WorkflowCatalog()
    manifest: dict[str, Any] = catalog.get(workflow_id, require_enabled=True)
    if workflow_id not in ELECTROCHEM_WORKFLOWS:
        raise ValueError(f"workflow is not handled by the electrochem adapter: {workflow_id}")
    required = ", ".join(manifest["required_parameters"]) or "none"
    extensions = ", ".join(manifest["input_contract"].get("extensions", [])) or "see manifest"
    return f"""
Land2Origin controlled workflow selected:
- workflow_id: {workflow_id}
- label: {manifest['label']}
- accepted source extensions: {extensions}
- required parameters: {required}
- delivery grade: {manifest['delivery_grade']}

Protocol:
1. Treat the workflow_id above as selected by the channel intent layer. Do not
   silently switch it. If the user's meaning conflicts with it, ask one short
   clarification question.
2. Inspect supplied attachments. Never infer sample_name, rate_label,
   cycle_numbers, rate_schedule, CE/E_C semantics, or scientific conclusions
   from a filename. Ask only for missing required parameters.
3. Before execution, show a compact summary containing workflow, source,
   parameters, and delivery grade. Execute only after the user explicitly
   confirms that summary in this Codex thread.
4. Create a schema 1.0 job JSON under /home/sd1/Land2Origin/runtime/ and call:
   cd /home/sd1/Land2Origin && PYTHONPATH=src python -m land2origin.cli execute <job.json>
   Do not invoke Origin scripts or configure arbitrary shell runners directly.
5. Success requires complete.json status=ok and the archived OPJU and PNG.
   Return both as native WeChat attachments with exactly one action block:
```codex-weixin-actions
{{"send":[{{"type":"image","path":"/absolute/archive/output.png"}},{{"type":"file","path":"/absolute/archive/output.opju"}}]}}
```
6. Never use a local path as the user-facing delivery method. If execution
   fails, report the actual failed stage and keep the staging path for diagnosis.
7. Do not route XRD through this workflow. XRD remains a separately guarded
   GSAS-II/Origin workflow.
""".strip()
