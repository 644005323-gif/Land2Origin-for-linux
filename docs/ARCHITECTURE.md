# Architecture

The repository is split by ownership:

- `config/workflows/`: one JSON manifest per workflow. Manifests contain the
  stable ID, input contract, required parameters, renderer and delivery grade.
- `src/land2origin/contracts.py`: strict schema 1.0 job parsing and path/run ID
  validation.
- `src/land2origin/catalog.py`: CRUD and recoverable archive for manifests.
- `src/land2origin/workflows/`: pure parsers and normalized data preparation.
- `src/land2origin/execution.py`: staging, source hashing, serialized Origin
  invocation, output validation and final archive.
- `deploy/origin/`: only the production-controlled Origin scripts needed by
  the VM. No credentials, Wine prefixes or installers belong here.
- `runtime/`: transient job JSON and local state; it is ignored by Git.

The channel adapter should create a standard job and call the CLI. It must not
construct arbitrary shell commands, select a renderer by filename, or bypass
the explicit confirmation gate.
