"""CLI.

  python -m scan init                      scaffold a sample org sheet
  python -m scan run --stage 1 [--only F]  scan + score  -> review/
  python -m scan run --stage 2             themes + memo -> out/
  python -m scan status                    progress and errors
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import config, io_xlsx, pipeline


def main() -> None:
    ap = argparse.ArgumentParser(prog="scan", description="Portable Claude-API research scan.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="write a sample input/organizations.xlsx")
    sub.add_parser("status", help="show progress and errors")

    ev = sub.add_parser("eval", help="trajectory eval on the golden set, and optionally judge a memo")
    ev.add_argument("--provider", choices=["anthropic", "openrouter"], default=None)
    ev.add_argument("--model", default=None, help="openrouter model id")
    ev.add_argument("--judge", type=Path, default=None, help="path to a memo .md to score on the rubric")

    run = sub.add_parser("run", help="run a stage")
    run.add_argument("--stage", type=int, choices=[1, 2], required=True)
    run.add_argument("--only", type=Path, default=None,
                     help="stage 1 only: scan just the orgs in this xlsx (incremental)")
    run.add_argument("--dry-run", action="store_true",
                     help="mock every model call, no key or network needed")
    run.add_argument("--provider", choices=["anthropic", "openrouter"], default=None,
                     help="which backend to run the agents on")
    run.add_argument("--model", default=None,
                     help="openrouter model id when --provider openrouter, e.g. openai/gpt-5")
    run.add_argument("--scope", choices=["africa", "global"], default=None,
                     help="africa focus (default) or a global scan")

    args = ap.parse_args()

    if args.cmd == "init":
        p = io_xlsx.write_sample_orgs()
        print(f"wrote {p}. Edit it, then: python -m scan run --stage 1")
        return
    if args.cmd == "status":
        pipeline.status()
        return
    if args.cmd == "eval":
        if args.provider:
            config.PROVIDER = args.provider
        if args.model:
            config.OR_MODEL = args.model
        config.require_key()
        from . import evaluate
        rows = asyncio.run(evaluate.trajectory())
        evaluate.print_trajectory(rows)
        if args.judge:
            res = asyncio.run(evaluate.judge_memo(args.judge.read_text(encoding="utf-8")))
            evaluate.print_rubric(res)
        return
    if args.cmd == "run":
        if getattr(args, "dry_run", False):
            config.DRY_RUN = True
            print("[dry-run] mocking all model calls, no API key or network used\n")
        if args.provider:
            config.PROVIDER = args.provider
        if args.model:
            config.OR_MODEL = args.model
        if args.scope:
            config.SCAN_MODE = args.scope
        if config.PROVIDER == "openrouter" and not config.DRY_RUN:
            print(f"[openrouter] running every stage on {config.OR_MODEL}\n")
        config.require_key()
        if not config.ORG_SHEET.exists() and not args.only:
            raise SystemExit("input/organizations.xlsx missing. Run: python -m scan init")
        if args.stage == 1:
            asyncio.run(pipeline.run_stage1(only=args.only))
        else:
            asyncio.run(pipeline.run_stage2())


if __name__ == "__main__":
    main()
