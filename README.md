# Land2Origin

Land2Origin is the product/workflow core for scientific plotting jobs. It keeps
workflow definitions, input contracts, execution, audit records and channel
delivery independent so new channels or renderers can be added without
changing the Origin runtime.

## Boundaries

```text
WeChatWatcher  ->  channel intent + Codex conversation + native delivery
Land2Origin    ->  workflow catalog + job contract + execution + archive
Origin-VM-Share -> staging and VM shared files only
Origin VM      -> Windows CPython/originpro/OriginExt -> Origin -> OPJU/PNG
```

XRD/GSAS-II remains a separately guarded workflow in WeChatWatcher. It is not
sent through the electrochemistry runner or its `series.csv` contract.

## Quick start

```bash
cd /home/sd1/Land2Origin
PYTHONPATH=src python -m land2origin.cli workflow list --all
PYTHONPATH=src python -m land2origin.cli workflow show first_cycle_charge_discharge
PYTHONPATH=src python -m land2origin.cli execute /absolute/path/job.json
```

The execute command requires `user_confirmed: true`, a unique `run_id` (or it
generates one), and absolute existing source paths. A successful job archives
the source, `complete.json`, Origin OPJU and Origin PNG under the configured
electrochemistry archive root.

## Tests

```bash
PYTHONPATH=src python tests/test_catalog.py
PYTHONPATH=src python tests/test_electrochem.py
PYTHONPATH=src python tests/test_integration.py
```
