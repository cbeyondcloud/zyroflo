"""A scanner the clock kills: what it printed survives, and so does the document.

Both gates bound their tool with `subprocess.run(..., timeout=...)`, whose
`TimeoutExpired` carries `.stdout` and `.stderr` and was escaping uncaught -
past a `prepass()` whose docstring says it never raises for tool trouble. The
cost was not only the hung tool's own output: the sibling gate's row went with
it and NO document was written, and a missing document is what
`suite.sca_problems` reads as "no gate result is recorded".

The command here PRINTS ON BOTH STREAMS and then sleeps past its timeout, so
the assertions are about a real `TimeoutExpired` carrying real partial output,
not about a fake that returns what the test wants to see.
"""
from __future__ import annotations

import sys

from tools.review import prepass as PP
from tools.review import sca as SCA


#: Prints to both streams, flushes, then outlives any timeout we set.
HUNG = ("import sys,time;"
        "sys.stdout.write('scanning component one\\n');"
        "sys.stderr.write('progress: 12 manifests read\\n');"
        "sys.stdout.flush();sys.stderr.flush();"
        "time.sleep(30)")


def test_run_tool_keeps_both_streams_when_the_clock_ends_the_command() -> None:
    code, out, err = PP.run_tool([sys.executable, "-c", HUNG], timeout=3)

    assert code is None, "a killed command has no exit status to report"
    assert "scanning component one" in out
    assert "progress: 12 manifests read" in err
    assert err.splitlines()[0].startswith("not run to completion"), (
        f"the reason must LEAD stderr, a reader takes a leading slice: {err!r}")


def test_a_hung_gate_still_writes_a_document_with_its_sibling_row(
        tmp_path, monkeypatch) -> None:
    """gitleaks hangs; ast-grep answers. The pass returns both rows."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    real = PP.run_tool

    def fake(cmd, timeout=300, env=None):
        if "gitleaks" in cmd[0]:
            return real([sys.executable, "-c", HUNG], timeout=3)
        return 0, "[]", ""

    monkeypatch.setattr(PP, "binary",
                        lambda name: (tmp_path / f"{name}-bin", ""))
    monkeypatch.setattr(PP, "run_tool", fake)

    doc = PP.prepass(tmp_path)

    rows = {t["name"]: t for t in doc["tools"]}
    assert "ast-grep" in rows, "the sibling gate's row must still reach the document"
    leak = rows["gitleaks"]
    assert leak["status"] == "not-run"
    assert "did not finish within 3s" in leak["detail"]
    assert "scanning component one" in leak["detail"]
    assert "progress: 12 manifests read" in leak["detail"]
    assert leak["detail"].startswith("not run to completion")


def test_a_hung_component_scan_is_not_run_and_keeps_what_it_printed(
        tmp_path) -> None:
    """The same, through `sca.scan_one`, whose tool is bounded by the same seam."""
    where = tmp_path / "component-one"
    where.mkdir()

    real = PP.run_tool
    calls = []

    def fake(cmd, timeout=300, env=None):
        calls.append(cmd)
        return real([sys.executable, "-c", HUNG], timeout=3)

    original = PP.run_tool
    try:
        PP.run_tool = fake
        result = SCA.scan_one(tmp_path / "osv", where, tmp_path)
    finally:
        PP.run_tool = original

    row = result["row"]
    assert row["component"] == "component-one"
    assert row["status"] == "not-run"
    assert "did not finish within 3s" in row["detail"]
    assert "scanning component one" in row["detail"]
    assert "progress: 12 manifests read" in row["detail"]
    assert len(calls) == 1, (
        "a hung command must not be retried - that spends the timeout twice")
