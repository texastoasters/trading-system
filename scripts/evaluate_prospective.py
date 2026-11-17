#!/usr/bin/env python3
"""Bounded research-only campaign commands; never submit orders or install timers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_command")


def _emit(value):
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _read_protocol(filename):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("invalid_protocol")
            result[key] = value
        return result

    path = Path(filename)
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_protocol")
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("invalid_protocol")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)


def main(argv=None, *, alpaca=None, public=None, provider=None, clock=None, execute_lab=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--protocol", required=True)
    register.add_argument("--output-dir", required=True)
    for name in ("tick", "report", "verify"):
        command = commands.add_parser(name)
        command.add_argument("campaign_path")
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as caught:
        return caught.code
    except ValueError:
        _emit({"status": "failed", "error": "invalid_command", "executable": False})
        return 2
    try:
        if arguments.command == "register":
            from prospective_evaluation.storage import register_campaign
            request = _read_protocol(arguments.protocol)
            path = register_campaign(arguments.output_dir, request, clock=clock)
            _emit({"status": "registered", "executable": False, "campaign_path": str(path)})
        elif arguments.command == "tick":
            from prospective_evaluation.runner import tick
            from prospective_evaluation.storage import load_campaign
            from prospective_evidence.transport import HttpGetTransport, SshAlpacaTransport
            if provider is None and execute_lab is None:
                from prospective_evaluation.execution import ManagedLabExecutor
                execute_lab = ManagedLabExecutor(load_campaign(arguments.campaign_path)["protocol"]["model"])
            result = tick(
                arguments.campaign_path,
                alpaca if alpaca is not None else SshAlpacaTransport(),
                public if public is not None else HttpGetTransport(), provider, clock=clock,
                execute_lab=execute_lab,
            )
            _emit(result)
            return 1 if result.get("status") in {"failed", "unavailable", "source_unavailable", "in_doubt"} else 0
        elif arguments.command == "report":
            from prospective_evaluation.runner import report_campaign
            _emit({"status": "reported", "executable": False,
                   "report": report_campaign(arguments.campaign_path)})
        else:
            from prospective_evaluation.runner import verify_campaign
            _emit({"status": "verified", "executable": False,
                   "verification": verify_campaign(arguments.campaign_path)})
        return 0
    except Exception:
        _emit({"status": "failed", "error": "operation_failed", "executable": False})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
