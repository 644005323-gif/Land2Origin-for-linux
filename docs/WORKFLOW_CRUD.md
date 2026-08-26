# Workflow CRUD

List, inspect, enable, disable and archive manifests through the CLI:

```bash
PYTHONPATH=src python -m land2origin.cli workflow list --all
PYTHONPATH=src python -m land2origin.cli workflow show cv
PYTHONPATH=src python -m land2origin.cli workflow disable cv
PYTHONPATH=src python -m land2origin.cli workflow enable cv
PYTHONPATH=src python -m land2origin.cli workflow update /path/to/cv.json
PYTHONPATH=src python -m land2origin.cli workflow delete cv --yes
```

Delete is recoverable: the manifest is moved to `config/workflows/.archive/`.
Adding a new workflow requires a manifest, a registered preparer/renderer,
contract tests and an independent real-data validation before its delivery
grade is promoted.
