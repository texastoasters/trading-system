#!/usr/bin/env python3
"""Collect and verify immutable prospective-evidence archives."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


MAX_EXPERIMENT_BYTES = 64 * 1024


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_command")


def _parser():
    parser = _ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    collect = subcommands.add_parser("collect", help="collect source evidence into an archive")
    collect.add_argument("--experiment", required=True)
    collect.add_argument("--output-dir", required=True)
    verify = subcommands.add_parser("verify", help="verify an archive wholly offline")
    verify.add_argument("collection_path")
    return parser


def _emit(value):
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _failure(code, **fields):
    return {"status": "failed", "error": code, "executable": False, **fields}


def _read_experiment(filename):
    path = Path(filename)
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_experiment")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("invalid_experiment")
            value[key] = item
        return value

    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_EXPERIMENT_BYTES + 1)
        if len(raw) > MAX_EXPERIMENT_BYTES:
            raise ValueError
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ValueError("invalid_experiment") from None
    try:
        from prospective_evidence.pipeline import prepare_experiment
        return prepare_experiment(value)
    except Exception:
        raise ValueError("invalid_experiment") from None


def _counts(bundle):
    counts = {}
    for item in bundle["evidence"]:
        kind = item["kind"]
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _failed_archive(output_dir, identity, capture, code):
    from prospective_evidence.archive import write_failed_collection
    try:
        path = write_failed_collection(output_dir, identity, capture, error_code=code)
    except Exception:
        _emit(_failure("storage_failed"))
        return 1
    _emit(_failure(code, path=str(path)))
    return 1


def _collect(arguments, *, alpaca, public, clock):
    try:
        experiment = _read_experiment(arguments.experiment)
    except Exception:
        _emit(_failure("invalid_experiment"))
        return 1
    identity = experiment["collection_id"]
    try:
        from prospective_evidence.acquire import collect_sources
        from prospective_evidence.transport import HttpGetTransport, SshAlpacaTransport
        capture = collect_sources(
            [item["symbol"] for item in experiment["universe"]],
            alpaca if alpaca is not None else SshAlpacaTransport(),
            public if public is not None else HttpGetTransport(),
            clock=clock,
        )
    except Exception:
        return _failed_archive(arguments.output_dir, identity, {"collection_id": identity}, "collection_failed")
    if any(source.get("complete") is not True for source in capture["sources"].values()):
        return _failed_archive(arguments.output_dir, identity, capture, "capture_incomplete")
    try:
        from prospective_evidence.archive import write_collection
        from prospective_evidence.pipeline import build_collection
        built = build_collection(capture, experiment)
    except Exception:
        return _failed_archive(arguments.output_dir, identity, capture, "collection_failed")
    try:
        path = write_collection(arguments.output_dir, built["bundle"], built["capture"])
    except Exception:
        _emit(_failure("storage_failed"))
        return 1
    _emit({
        "status": "collected", "executable": False, "collection_path": str(path),
        "bundle_path": str(path / "bundle.json"), "evidence_counts": _counts(built["bundle"]),
    })
    return 0


def _verify(path_value):
    path = Path(path_value)
    try:
        from prospective_evidence.pipeline import reconstruct_collection
        result = reconstruct_collection(path)
    except Exception:
        _emit(_failure("verification_failed", collection_path=str(path)))
        return 1
    if result["verification_kind"] != "semantic_reconstruction":
        _emit(_failure(
            result["manifest"]["error"], collection_path=str(path),
            verification_kind=result["verification_kind"],
        ))
        return 1
    _emit({
        "status": "verified", "executable": False, "collection_path": str(path),
        "bundle_path": str(path / "bundle.json"),
        "verification_kind": result["verification_kind"], "evidence_counts": _counts(result["bundle"]),
    })
    return 0


def main(argv=None, *, alpaca=None, public=None, clock=None):
    try:
        arguments = _parser().parse_args(argv)
    except SystemExit as caught:
        return caught.code
    except ValueError:
        _emit(_failure("invalid_command"))
        return 2
    if arguments.command == "collect":
        return _collect(arguments, alpaca=alpaca, public=public, clock=clock)
    return _verify(arguments.collection_path)


if __name__ == "__main__":
    raise SystemExit(main())
