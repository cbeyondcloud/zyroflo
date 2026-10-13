"""T618: a link pass that raises loses every row it would have emitted.

The build said so in a warning and exited 0, so "no findings" and "the pass that finds them
crashed" read alike to anything that reads the exit code - CI first - while a contract violation
already exited non-zero. Batch 15b put `canonical-unmatched`, `apart-unmatched` and
`apart-duplicate` behind that door. The build now fails as a contract violation does: non-zero
even at status `ok`, `--empty-ok` notwithstanding, on a full build and on `--from-snapshot`.

Spawned throughout: a CLI's contract is a process (CLAUDE.md).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph.tests._estate import compose, lay_down, producer, svc  # noqa: E402

#: A profile with one link pass. `{mode}`: "raise", "violate" (a row missing the field its kind
#: promises) or "clean".
PROFILE = '''\
from tools.graph.core.model import ExtractResult, unresolved_row


class _Pass:
    name = "stub-pass"
    UNRESOLVED_FIELDS = {{"stub-finding": ("service",)}}

    def link(self, ctx, nodes, edges):
        res = ExtractResult()
        if {mode!r} == "raise":
            raise RuntimeError("the pass broke")
        if {mode!r} == "violate":
            res.unresolved.append(unresolved_row(self.name, "", "stub-finding", "", 0,
                                                 "names no service"))
        return res


LINK_PASSES = (_Pass(),)
'''


#: An estate that builds at status `ok` - it has edges - so a clean-build verdict has nothing
#: but the link pass to fail on (T639).
EDGED = {"a": {"app.py": producer("TOPIC"),
               "docker-compose.yml": compose(svc("a", build=".", value="orders"))},
         "b": {"app.py": "print('b')\n"}}


def workspace(where: Path, mode: str, estate: dict | None = None) -> None:
    toml = lay_down(where, estate or {"a": {"app.py": "print('a')\n"},
                                      "b": {"app.py": "print('b')\n"}})
    toml.write_bytes(toml.read_bytes().replace(b'profile = ""', b'profile = "./prof"'))
    profile(where, mode)


def profile(where: Path, mode: str) -> None:
    prof = where / "ws" / "prof"
    prof.mkdir(exist_ok=True)
    (prof / "__init__.py").write_bytes(PROFILE.format(mode=mode).encode("utf-8"))


def build(where: Path, *flags: str) -> subprocess.CompletedProcess:
    """The build as a person runs it. `--empty-ok` throughout: it excuses an empty graph and
    must never excuse a crashed pass. No bytecode: the profile is rewritten between runs at the
    same size, and a cached copy of the last one must not answer for it."""
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--out", str(where / "out"), "--run-id",
         "fixed", "--empty-ok", *flags],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_a_link_pass_that_raises_fails_the_build_and_the_remedy_passes(tmp_path):
    workspace(tmp_path, "raise")
    proc = build(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "link pass 'stub-pass' failed" in out and "RuntimeError: the pass broke" in out, out
    assert "findings are absent, not clean" in out, out
    assert "Traceback (most recent call last)" not in out, out
    report = json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))
    assert report["link_failures"] == {"stub-pass": "RuntimeError: the pass broke"}
    profile(tmp_path, "clean")                # the remedy: fix the pass, then rebuild
    proc = build(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_link_pass_that_raises_fails_a_load_from_the_snapshot(tmp_path):
    workspace(tmp_path, "clean")
    published = build(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    profile(tmp_path, "raise")
    proc = build(tmp_path, "--from-snapshot")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "link pass 'stub-pass' failed" in out and "findings are absent, not clean" in out, out
    profile(tmp_path, "clean")
    proc = build(tmp_path, "--from-snapshot")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_publish_refusal_names_a_link_pass_as_one(tmp_path):
    """It counted the crashed pass as "1 extractor failure(s)" and said the facts were
    incomplete - a reader of it would look for an extractor that never failed."""
    workspace(tmp_path, "raise")
    proc = build(tmp_path, "--publish-snapshot")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    refusal = next(line for line in out.split("\n") if line.startswith("snapshot: refused"))
    assert "1 link pass failure(s)" in refusal and "extractor failure" not in refusal, refusal
    assert "fix or remove the pass" in refusal, refusal


def test_a_link_pass_contract_violation_fails_a_load_from_the_snapshot(tmp_path):
    """The full build already exits 2 on a link pass's contract violation; a load from the
    snapshot printed nothing and exited 0, the same pass's same defect."""
    workspace(tmp_path, "clean")
    published = build(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    profile(tmp_path, "violate")
    proc = build(tmp_path, "--from-snapshot")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "contract:" in out and "stub-pass" in out, out
    # Review round 1: headlined by its class, as the build headlines it (T637) - on the console
    # and in the committed report.
    headline = "1 unresolved row(s) do not carry the fields their kind promises"
    assert f"!! {headline}" in out, out
    report = json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))
    assert [w.split(". ")[0] for w in report["warnings"]] == [headline], report


WHY = "RuntimeError: the pass broke"
PROBLEM = f"link pass failed: stub-pass ({WHY})"


def test_the_validation_report_names_a_crashed_link_pass(tmp_path):
    """T639 (batch 17's review, B17-1; pulled into 1.0 by decision 36): since T618 the build
    exits 2 for a crashed link pass, and the validation report the SOP's step 5 writes beside
    it still said `zero_silent_drops: PASS` - two artifacts a repository commits, disagreeing.
    The report counts each crashed pass as a problem now, and names it; the remedy followed
    (fix the pass, rebuild) grades clean."""
    def graded() -> str:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "graph" / "validate.py"), "--graph-dir",
             str(tmp_path / "out")],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.stdout + proc.stderr

    workspace(tmp_path, "raise", EDGED)
    assert build(tmp_path).returncode == 2
    report = json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok", report["status"]     # nothing else to fail on
    out = graded()
    assert "zero_silent_drops: FAIL" in out and f"!! {PROBLEM}" in out, out
    profile(tmp_path, "clean")
    assert build(tmp_path).returncode == 0
    assert "zero_silent_drops: PASS" in graded()


def test_the_clean_build_verdict_counts_each_crashed_link_pass_once():
    """T639: a report written since batch 17 records `link_failures`; one written before it,
    `links[].failed` alone (empty for a pass that ran); one holding both names the pass once."""
    from tools.graph import validate

    base = {"status": "ok", "extractors": [], "contract_violations": []}
    since = {**base, "link_failures": {"stub-pass": WHY}}
    before = {**base, "links": [{"name": "stub-pass", "failed": WHY},
                                {"name": "fine-pass", "failed": ""}]}
    for report in (since, before, {**since, "links": before["links"]}):
        verdict = validate.compute_clean_build(report)
        assert (verdict["met"], verdict["problems"]) == (False, [PROBLEM]), report
    assert validate.compute_clean_build(base)["met"] is True


def test_the_mcp_status_tool_names_a_crashed_link_pass(tmp_path):
    """T639, its second door (review B17-13): `graph_status` answered `build_status: ok` over
    the same report, the pass named nowhere. It carries the clean-build verdict now - the
    rule `validate` grades by, on the report the tool already reads - and a remedy followed
    (fix the pass, rebuild) reads clean."""
    from tools.graph import mcp_server as M

    workspace(tmp_path, "raise", EDGED)
    assert build(tmp_path).returncode == 2
    crashed = M.GraphService(tmp_path / "out").status()["clean_build"]
    assert (crashed["met"], crashed["problems"]) == (False, [PROBLEM]), crashed
    profile(tmp_path, "clean")
    assert build(tmp_path).returncode == 0
    assert M.GraphService(tmp_path / "out").status()["clean_build"]["met"] is True
