#!/usr/bin/env python3
"""SDLC harness PostToolUse hook — run the project's linter after an edit.

Non-blocking: reads `lint_command` from .sdlc/profile.yml, runs it (scoped to the edited
file when possible), and surfaces any issues as context. No-op when lint_command is blank.
Always exits 0 so it never blocks editing. Stdlib only.
"""
import json
import os
import re
import subprocess
import sys

# Linter output may contain non-ASCII; never crash on a cp1252 console.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".cs", ".java", ".cpp", ".cc", ".c", ".h")


def _lint_command(profile_path: str) -> str:
    try:
        for line in open(profile_path, encoding="utf-8"):
            m = re.match(r'\s*lint_command:\s*"(.*)"\s*(#.*)?$', line)
            if m:
                return m.group(1).replace('\\"', '"').strip()
    except OSError:
        pass
    return ""


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    profile = os.path.join(root, ".sdlc", "profile.yml")
    if not os.path.exists(profile):
        return 0
    lint = _lint_command(profile)
    if not lint:
        return 0

    # Scope to the edited file when the linter targets the current dir (faster, quieter).
    data = {}
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        pass
    fp = (data.get("tool_input") or {}).get("file_path")
    cmd = lint
    if fp and fp.endswith(_EXTS):
        cmd = re.sub(r"\s\.\s*$", f' "{fp}"', lint) or lint

    try:
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0 and out:
            print(f"[sdlc autolint] `{cmd}` reported issues:\n{out[:2000]}")
    except Exception:
        pass  # never block an edit on lint failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
