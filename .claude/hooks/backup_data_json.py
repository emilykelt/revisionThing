#!/usr/bin/env python3
"""PreToolUse hook: snapshot data/*.json before Claude edits it.

The whole app state lives in hand-edited JSON under data/ (history.json,
knowledge.json, pp_progress.json, ...). A bad write corrupts irreplaceable
revision data, so before any Edit/Write/MultiEdit to one of those files we
copy it to data/.backups/<name>.<UTC-timestamp>.json. Never blocks (exit 0).
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone

KEEP_PER_FILE = 10  # prune older snapshots so backups don't grow unbounded


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input — don't get in the way

    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path:
        return 0

    norm = path.replace(os.sep, "/")
    if "/data/" not in norm or not norm.endswith(".json"):
        return 0
    if "/data/.backups/" in norm:  # don't back up backups
        return 0
    if not os.path.isfile(path):
        return 0  # new file being created — nothing to snapshot

    data_dir = os.path.dirname(path)
    backups = os.path.join(data_dir, ".backups")
    os.makedirs(backups, exist_ok=True)

    name = os.path.basename(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(backups, f"{name}.{stamp}")
    try:
        shutil.copy2(path, dest)
    except Exception as e:
        print(f"[backup_data_json] could not back up {name}: {e}", file=sys.stderr)
        return 0

    # Prune: keep only the newest KEEP_PER_FILE snapshots for this file.
    prefix = f"{name}."
    snaps = sorted(
        (f for f in os.listdir(backups) if f.startswith(prefix)),
        reverse=True,
    )
    for stale in snaps[KEEP_PER_FILE:]:
        try:
            os.remove(os.path.join(backups, stale))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
