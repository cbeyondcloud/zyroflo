#!/usr/bin/env python3
"""SDLC harness SessionStart hook — surface any in-progress run so it can be resumed.

Scans .sdlc/plans/*.md for plans whose Status is not "Fully Implemented" and prints a
one-line reminder per active run. Read-only, stdlib only, always exits 0.
"""
import glob
import os
import re
import sys

# Plan Status cells contain emoji; never crash on a cp1252 console.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _cell(text: str, field: str) -> str:
    m = re.search(rf"\|\s*{re.escape(field)}\s*\|\s*(.*?)\s*\|", text)
    return m.group(1).strip() if m else ""


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    active = []
    for path in glob.glob(os.path.join(root, ".sdlc", "plans", "*.md")):
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        status = _cell(text, "Status")
        if status and "Fully Implemented" not in status:
            active.append((os.path.basename(path), _cell(text, "Phase") or "?", status))

    if active:
        print("[sdlc] In-progress SDLC run(s) — resume with `/sdlc resume`:")
        for name, phase, status in active:
            print(f"  - {name}: Phase {phase} ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
