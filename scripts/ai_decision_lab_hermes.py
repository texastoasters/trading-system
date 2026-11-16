#!/usr/bin/env python3
"""Run the lab using Hermes' actual managed dependency closure, not an old venv.

Hermes' --print-runtime-command is the source of truth. Fail closed if its
bootstrap format changes; never install packages or rewrite Hermes settings.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ENTRYPOINT = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"


def managed_command(runtime_command: list, script: Path, arguments: list[str]) -> list[str]:
    if (not isinstance(runtime_command, list) or not all(isinstance(s, str) for s in runtime_command)
            or len(runtime_command) != 4 or runtime_command[1:3] != ["-I", "-c"]
            or runtime_command[3].count(ENTRYPOINT) != 1):
        raise ValueError("unsupported_hermes_runtime_format")
    prefix = runtime_command[3].replace(ENTRYPOINT, "")
    code = (prefix + f"sys.path.insert(0, {str(script.parent)!r}); "
            + f"sys.argv = [{str(script)!r}] + sys.argv[1:]; "
            + f"runpy.run_path({str(script)!r}, run_name='__main__')")
    return [*runtime_command[:3], code, *arguments]


def main(arguments=None):
    try:
        response = subprocess.run(["hermes", "--print-runtime-command"], capture_output=True,
                                  text=True, timeout=60, check=True)
        script = Path(__file__).resolve().with_name("ai_decision_lab.py")
        command = managed_command(json.loads(response.stdout), script,
                                  sys.argv[1:] if arguments is None else arguments)
        return subprocess.run(command, check=False).returncode
    except Exception:
        # No exception bodies: a malformed external launcher must not echo secrets.
        print(json.dumps({"status": "failed", "failure_type": "managed_runtime_unavailable", "executable": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
