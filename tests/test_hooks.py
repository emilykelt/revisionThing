"""Tests for the data-safety hook scripts (.claude/hooks/*.py).

They run as separate processes driven by a JSON payload on stdin, so we exercise
them the same way Claude Code does: pipe a payload, check exit code and effects."""
import json
import os
import subprocess
import sys

HOOKS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claude", "hooks"
)
BACKUP = os.path.join(HOOKS, "backup_data_json.py")
VALIDATE = os.path.join(HOOKS, "validate_data_json.py")


def _run(script, file_path):
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}})
    return subprocess.run(
        [sys.executable, script],
        input=payload, capture_output=True, text=True,
    )


def _data_file(tmp_path, name, content):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(content)
    return f


# ---------------------------------------------------------------- backup hook

def test_backup_snapshots_existing_data_file(tmp_path):
    f = _data_file(tmp_path, "knowledge.json", '{"t1": 1}')
    res = _run(BACKUP, f)
    assert res.returncode == 0
    backups = list((tmp_path / "data" / ".backups").glob("knowledge.json.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == '{"t1": 1}'


def test_backup_ignores_non_data_file(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("print(1)")
    res = _run(BACKUP, f)
    assert res.returncode == 0
    assert not (tmp_path / ".backups").exists()


def test_backup_skips_new_file(tmp_path):
    # File doesn't exist yet (Write creating it) → nothing to snapshot, exit 0.
    res = _run(BACKUP, tmp_path / "data" / "new.json")
    assert res.returncode == 0


def test_backup_prunes_to_keep_limit(tmp_path):
    f = _data_file(tmp_path, "history.json", "[]")
    for _ in range(13):
        _run(BACKUP, f)
    backups = list((tmp_path / "data" / ".backups").glob("history.json.*"))
    assert len(backups) <= 10


# ---------------------------------------------------------------- validate hook

def test_validate_passes_valid_json(tmp_path):
    f = _data_file(tmp_path, "planner.json", '{"ok": true}')
    res = _run(VALIDATE, f)
    assert res.returncode == 0


def test_validate_blocks_broken_json(tmp_path):
    f = _data_file(tmp_path, "planner.json", '{ "broken": ')
    res = _run(VALIDATE, f)
    assert res.returncode == 2
    assert "Invalid JSON" in res.stderr


def test_validate_ignores_non_data_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("not json {")
    res = _run(VALIDATE, f)
    assert res.returncode == 0
