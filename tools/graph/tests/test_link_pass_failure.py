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

from tools.graph.tests._estate import lay_down  # noqa: E402

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


def workspace(where: Path, mode: str) -> None:
    toml = lay_down(where, {"a": {"app.py": "print('a')\n"}, "b": {"app.py": "print('b')\n"}})
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
