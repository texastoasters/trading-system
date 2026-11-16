#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from decision_lab import replay_run, run_lab, validate_bundle


def _load(path: str) -> dict:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def _print(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T2 broker-isolated AI decision lab")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate an input bundle without inference")
    validate.add_argument("input")
    validate.add_argument("--now", help="timezone-aware ISO8601 validation clock")

    run = sub.add_parser("run", help="run bounded inference and persist artifacts")
    run.add_argument("input")
    run.add_argument("--model", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--now", help="timezone-aware ISO8601 clock (test/diagnostic use)")
    run.add_argument("--no-research", action="store_true")
    run.add_argument("--no-critic", action="store_true")

    replay = sub.add_parser("replay", help="verify a saved run without inference")
    replay.add_argument("run_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            value = validate_bundle(_load(args.input), now=args.now)
            _print({
                "valid": True,
                "run_id": value["run_id"],
                "mode": value["mode"],
                "executable": False,
            })
            return 0
        if args.command == "replay":
            _print(replay_run(args.run_dir))
            return 0

        # Deliberately lazy: offline validate/replay have no Hermes/OpenAI dependency.
        providers = importlib.import_module("decision_lab.providers")

        if hasattr(providers, "create_provider"):
            provider = getattr(providers, "create_provider")(args.model)
        elif hasattr(providers, "NousProvider"):
            provider = getattr(providers, "NousProvider")(args.model)
        else:
            raise RuntimeError("provider adapter exposes neither create_provider nor NousProvider")
        result = run_lab(
            _load(args.input),
            provider,
            args.output,
            now=args.now,
            research=not args.no_research,
            critic=not args.no_critic,
        )
        _print(result)
        return 0 if result["status"] == "accepted" else 2
    except Exception:
        if args.command == "replay":
            failure_type = "replay_error"
        elif args.command == "validate":
            failure_type = "input_error"
        else:
            failure_type = "provider_or_run_error"
        _print({
            "status": "failed",
            "failure_type": failure_type,
            "message": "operation failed; sensitive exception details suppressed",
            "executable": False,
        })
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
