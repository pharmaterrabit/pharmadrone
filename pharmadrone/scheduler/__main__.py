"""CLI: python -m pharmadrone.scheduler ..."""
from __future__ import annotations
import argparse
import json
import sys

from .config import source_names
from .orchestrator import run_sources, status


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m pharmadrone.scheduler")
    sub = p.add_subparsers(dest="command", required=True)
    due = sub.add_parser("run-due", help="Run all database-due source jobs")
    due.add_argument("--dry-run", action="store_true")
    due.add_argument("--lookback-days", type=int)
    one = sub.add_parser("run-source", help="Run one selected source")
    one.add_argument("source", choices=source_names())
    one.add_argument("--force", action="store_true")
    one.add_argument("--dry-run", action="store_true")
    one.add_argument("--lookback-days", type=int)
    sub.add_parser("status", help="Show durable scheduler/source state")
    retry = sub.add_parser("retry-failed", help="Retry sources in failed/degraded/partial state")
    retry.add_argument("--dry-run", action="store_true")
    retry.add_argument("--lookback-days", type=int)
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            result = status()
        elif args.command == "run-due":
            result = run_sources(dry_run=args.dry_run, trigger_type="scheduled", lookback_days=args.lookback_days)
        elif args.command == "run-source":
            result = run_sources(selected=[args.source], force=args.force, dry_run=args.dry_run,
                                 trigger_type="manual", lookback_days=args.lookback_days)
        else:
            result = run_sources(dry_run=args.dry_run, trigger_type="retry-failed",
                                 lookback_days=args.lookback_days, failed_only=True)
        print(json.dumps(result, indent=2, default=str))
        if args.command == "status":
            return 2 if str(result.get("scheduler_status") or "").lower() in {"failed", "degraded", "partial"} else 0
        return 0 if result.get("status") in {"Healthy", "Dry run"} else 2
    except Exception as exc:
        print(json.dumps({"status": "Failed", "error": type(exc).__name__, "message": str(exc)[:240]}, indent=2), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
