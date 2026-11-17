"""Deployment must respect containment before the legacy updater runs."""
from pathlib import Path
import runpy
from unittest.mock import patch

import deploy_guard
import pytest
import yaml


def test_hold_blocks_updater(tmp_path, capsys):
    (tmp_path / "HOLD").write_text("intentional containment")
    marker = tmp_path / "updater-called"
    updater = tmp_path / "update"
    updater.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    updater.chmod(0o700)
    assert deploy_guard.main(tmp_path, updater) == 78
    assert not marker.exists()
    assert "HOLD" in capsys.readouterr().err


def test_workflow_streams_guard_before_legacy_updater():
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/deploy.yml").read_text()
    )
    tag = workflow["jobs"]["tag"]
    assert " ".join(tag["if"].split()) == (
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.event == 'push' && "
        "github.event.workflow_run.head_branch == 'main' && "
        "github.event.workflow_run.head_repository.full_name == github.repository"
    )
    assert workflow["jobs"]["deploy"] == {
        "name": "Deploy to production",
        "runs-on": "ubuntu-latest",
        "needs": ["tag"],
        "permissions": {"contents": "read"},
        "steps": [
            {
                "uses": "actions/checkout@v4",
                "with": {
                    "ref": "${{ github.event.workflow_run.head_sha }}",
                    "persist-credentials": False,
                },
            },
            {
                "uses": "webfactory/ssh-agent@v0.9.0",
                "with": {"ssh-private-key": "${{ secrets.DEPLOY_SSH_KEY }}"},
            },
            {
                "name": "Deploy via SSH",
                "env": {"DEPLOY_HOST": "${{ secrets.DEPLOY_HOST }}"},
                "run": "ssh -o StrictHostKeyChecking=no \\\n    -o ConnectTimeout=30 \\\n    linuxuser@\"$DEPLOY_HOST\" \\\n    'python3 -' < scripts/deploy_guard.py\n",
            },
        ],
    }


@pytest.mark.parametrize("kind", ["file", "directory", "dangling_link"])
def test_all_hold_marker_types_block(tmp_path, kind):
    marker = tmp_path / "HOLD"
    if kind == "file":
        marker.write_text("")
    elif kind == "directory":
        marker.mkdir()
    else:
        marker.symlink_to(tmp_path / "nonexistent")
    assert deploy_guard.main(tmp_path, tmp_path / "must-not-run") == 78


@pytest.mark.parametrize("kind", ["missing", "not_directory", "denied"])
def test_unknown_containment_fails_closed(tmp_path, kind, monkeypatch, capsys):
    root = tmp_path / "containment"
    if kind == "not_directory":
        root.write_text("")
    if kind == "denied":
        def deny(_):
            raise PermissionError("unreadable")
        monkeypatch.setattr(deploy_guard.os, "scandir", deny)
    assert deploy_guard.main(root, tmp_path / "must-not-run") == 78
    assert "state unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("status", [0, 42])
def test_explicitly_clear_state_runs_real_updater_and_propagates_exit(tmp_path, status):
    root = tmp_path / "containment"
    root.mkdir()
    (root / "receipt.json").write_text("{}")
    called = tmp_path / "called"
    updater = tmp_path / "update"
    updater.write_text(f"#!/bin/sh\nprintf 'called' > '{called}'\nexit {status}\n")
    updater.chmod(0o700)
    assert deploy_guard.main(root, updater) == status
    assert called.read_text() == "called"


@pytest.mark.parametrize("signal_name, expected", [("TERM", 143), ("KILL", 137)])
def test_signal_termination_uses_conventional_shell_exit(tmp_path, signal_name, expected):
    root = tmp_path / "containment"
    root.mkdir()
    updater = tmp_path / "update"
    updater.write_text(f"#!/bin/sh\nkill -{signal_name} $$\n")
    updater.chmod(0o700)
    assert deploy_guard.main(root, updater) == expected


def test_default_updater_is_current_ssh_users_helper(tmp_path, monkeypatch):
    root = tmp_path / "containment"
    root.mkdir()
    updater = tmp_path / ".local/bin/update-trading-system"
    updater.parent.mkdir(parents=True)
    updater.write_text("#!/bin/sh\nexit 19\n")
    updater.chmod(0o700)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert deploy_guard.main(root) == 19


def test_unavailable_updater_fails_closed(tmp_path, capsys):
    assert deploy_guard.main(tmp_path, tmp_path / "missing-updater") == 78
    assert "updater unavailable" in capsys.readouterr().err


def test_streamed_cli_blocks_when_state_unavailable(capsys):
    with patch("os.scandir", side_effect=PermissionError("denied")):
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(Path(deploy_guard.__file__)), run_name="__main__")
    assert result.value.code == 78
    assert "state unavailable" in capsys.readouterr().err
