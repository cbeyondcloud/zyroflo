"""DIAGNOSTIC (throwaway, T365): why does vendored ast-grep 0.45.1 return
findings:0 for a planted breakpoint() on Linux CI while it FIRES on Windows?

Reproduces BOTH prepass modes (no-diff -> scans target; diff -> scans a
/tmp scratch copy) with the real binary, plus raw `sg` probes and a git-config
dump, then fails unconditionally so the whole dump lands in the CI log. Compare
the Linux-CI dump against the Windows-local baseline. NOT FOR MERGE.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.review import prepass as PP

DIFF = (
    "diff --git a/planted.py b/planted.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/planted.py\n"
    "@@ -0,0 +1,3 @@\n"
    "+def go():\n"
    "+    breakpoint()\n"
    "+    return 1\n"
)


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return f"exit={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
    except Exception as exc:  # noqa: BLE001
        return f"EXC={type(exc).__name__}: {exc}"


def _astgrep_row(result):
    for t in result.get("tools", []):
        if t.get("name") == "ast-grep":
            return t
    return None


def test_zz_diag_astgrep():
    L = [f"platform={sys.platform} os.name={os.name} cwd={os.getcwd()}"]
    exe, why = PP.binary("sg")
    L.append(f"binary(sg) exe={exe!r} why={why!r}")
    if exe is None:
        pytest.fail("VENDORED sg ABSENT:\n" + "\n".join(L))
    exe = str(exe)
    L.append("VERSION: " + _run([exe, "--version"]))

    # git ignore context that ast-grep's walker honours
    L.append("git global excludesfile: " + _run(["git", "config", "--global", "--get", "core.excludesfile"]))
    for p in ("~/.config/git/ignore", "~/.gitignore"):
        ep = Path(os.path.expanduser(p))
        L.append(f"  {p} exists={ep.exists()}" + (f" bytes={ep.read_bytes()[:200]!r}" if ep.exists() else ""))

    target = Path(tempfile.mkdtemp(prefix="t365tgt_"))
    (target / "planted.py").write_text("def go():\n    breakpoint()\n    return 1\n", encoding="utf-8")
    L.append(f"target={target} listing={_run(['ls', '-la', str(target)]) if os.name != 'nt' else os.listdir(target)}")

    # ── Faithful repro 1: prepass NO-diff (what the reverted T345 test did) ──
    try:
        r1 = PP.prepass(target)
        L.append(f"PREPASS no-diff ast-grep row: {_astgrep_row(r1)}")
        L.append(f"  scope={r1.get('scope')}")
    except Exception as exc:  # noqa: BLE001
        L.append(f"PREPASS no-diff EXC={type(exc).__name__}: {exc}")

    # ── Faithful repro 2: prepass WITH diff (what the real P6 gate does) ────
    try:
        r2 = PP.prepass(target, diff_text=DIFF)
        L.append(f"PREPASS diff ast-grep row: {_astgrep_row(r2)}")
        L.append(f"  scope={r2.get('scope')}")
    except Exception as exc:  # noqa: BLE001
        L.append(f"PREPASS diff EXC={type(exc).__name__}: {exc}")

    # ── Raw probes ─────────────────────────────────────────────────────────
    d = Path(tempfile.mkdtemp(prefix="t365raw_"))
    f = d / "planted.py"
    f.write_text("def go():\n    breakpoint()\n    return 1\n", encoding="utf-8")
    joined, sources = PP.collect_rules(Path("."), [])
    single = Path("tools/review/rules/python.yml").read_text(encoding="utf-8").strip()
    L.append(f"collect_rules sources={sources}")
    L.append("A [scan --inline-rules JOINED --json=compact DIR]: " + _run([exe, "scan", "--inline-rules", joined, "--json=compact", str(d)]))
    L.append("B [scan --inline-rules SINGLE --json=compact DIR]:  " + _run([exe, "scan", "--inline-rules", single, "--json=compact", str(d)]))
    L.append("C [scan --inline-rules SINGLE --json=compact FILE]: " + _run([exe, "scan", "--inline-rules", single, "--json=compact", str(f)]))
    L.append("D [run -p breakpoint() -l python --json=compact FILE]: " + _run([exe, "run", "-p", "breakpoint()", "-l", "python", "--json=compact", str(f)]))
    L.append("E [scan --inline-rules JOINED DIR human]: " + _run([exe, "scan", "--inline-rules", joined, str(d)]))
    L.append("F [scan SINGLE --json=compact --no-ignore ALL DIR]: " + _run(
        [exe, "scan", "--inline-rules", single, "--json=compact",
         "--no-ignore", "hidden", "--no-ignore", "dot", "--no-ignore", "exclude",
         "--no-ignore", "global", "--no-ignore", "parent", "--no-ignore", "vcs", str(d)]))

    pytest.fail("T365 AST-GREP DIAGNOSTIC DUMP:\n" + "\n".join(L))
