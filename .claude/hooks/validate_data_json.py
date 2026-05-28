#!/usr/bin/env python3
"""PostToolUse hook: validate data/*.json right after Claude writes it.

Catches a malformed write the moment it happens — before the Flask app tries
to load the file and crashes (or silently serves broken state). On invalid
JSON we exit 2, which feeds the error back to Claude so it can fix the file.
"""
import json
import os
import sys


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path:
        return 0

    norm = path.replace(os.sep, "/")
    if "/data/" not in norm or not norm.endswith(".json"):
        return 0
    if "/data/.backups/" in norm:
        return 0
    if not os.path.isfile(path):
        return 0

    try:
        with open(path, "r") as f:
            json.load(f)
    except json.JSONDecodeError as e:
        print(
            f"Invalid JSON written to {os.path.basename(path)}: "
            f"line {e.lineno} col {e.colno} — {e.msg}. "
            f"Fix the file so it parses (a pre-edit snapshot is in data/.backups/).",
            file=sys.stderr,
        )
        return 2  # surfaces stderr back to Claude
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
