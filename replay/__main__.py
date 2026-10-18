"""Run with ``python -m replay PLAN OUTPUT --execute``."""

import argparse
import json
import sys

from .runner import ExecutionNotAllowed, ReplayValidationError, export_plan, run_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Execute bounded literal-loopback HTTP replay")
    parser.add_argument("plan", help="validated replay plan JSON")
    parser.add_argument("output", help="new output directory; parent must exist")
    parser.add_argument("--execute", action="store_true", help="explicitly authorize local HTTP execution")
    parser.add_argument("--export", action="store_true", help="export a standalone runner and plan without execution")
    args = parser.parse_args(argv)
    try:
        if args.export and args.execute:
            parser.error("--export and --execute are mutually exclusive")
        result = export_plan(args.plan, args.output) if args.export else run_plan(args.plan, args.output, allow_execute=args.execute)
    except (ExecutionNotAllowed, ReplayValidationError, OSError) as exc:
        print(f"replay refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "run_id": result["run_id"],
                      "output_dir": result["output_dir"]}))
    return 0 if result["status"] in {"passed", "exported"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
