#!/usr/bin/env python3
"""Project-environment entrypoint; only inference uses the managed SDK subprocess.

Do not re-execute the campaign in Hermes' environment: evidence dependencies
belong to the project, not the SDK closure. The tick's managed lab executor
selects the existing Nous runtime lazily when a decision is actually due.
"""
from evaluate_prospective import main


if __name__ == "__main__":
    raise SystemExit(main())
