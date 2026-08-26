from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import CatalogError, WorkflowCatalog
from .contracts import JobError, WorkflowJob
from .execution import execute


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _workflow(args: argparse.Namespace) -> int:
    catalog = WorkflowCatalog(args.catalog) if args.catalog else WorkflowCatalog()
    if args.action == "list":
        _print(catalog.list(include_disabled=args.all))
    elif args.action == "show":
        _print(catalog.get(args.workflow_id))
    elif args.action in {"add", "update"}:
        value = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        _print(catalog.put(value, replace=args.action == "update"))
    elif args.action in {"enable", "disable"}:
        _print(catalog.set_enabled(args.workflow_id, args.action == "enable"))
    elif args.action == "delete":
        if not args.yes:
            raise CatalogError("delete is recoverable but requires --yes")
        _print({"archived": str(catalog.archive(args.workflow_id))})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="land2origin")
    sub = parser.add_subparsers(dest="command", required=True)
    workflows = sub.add_parser("workflow", help="workflow catalog CRUD")
    workflows.add_argument("--catalog", default=None)
    actions = workflows.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--all", action="store_true")
    for name in ("show", "enable", "disable", "delete"):
        action = actions.add_parser(name)
        action.add_argument("workflow_id")
        if name == "delete":
            action.add_argument("--yes", action="store_true")
    for name in ("add", "update"):
        action = actions.add_parser(name)
        action.add_argument("manifest")
    run = sub.add_parser("execute", help="execute one confirmed workflow job")
    run.add_argument("job")
    run.add_argument("--timeout", type=int, default=180)
    run.add_argument("--catalog", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "workflow":
            return _workflow(args)
        catalog = WorkflowCatalog(args.catalog) if args.catalog else WorkflowCatalog()
        result = execute(WorkflowJob.load(args.job), catalog=catalog, timeout=args.timeout)
        _print(result)
        return 0 if result.get("status") == "ok" else 2
    except (CatalogError, JobError, OSError, json.JSONDecodeError, ValueError) as exc:
        _print({"status": "invalid", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
