"""Batch-2 review, F4-F6: a pinned artifact that differs from its pin only in line endings.

`state.drift` reports such a file as its own kind, `converted`, with the lossless remedy as
its detail (T483). It is a kind rather than a `changed` with a detail because the callers
ACT on `changed`, and each of the four must still do the right thing:

| caller                          | what must happen for `converted`                      |
|---------------------------------|-------------------------------------------------------|
| `state.verify_artifacts`        | its own sentence, plus the remedy                     |
| `approve.verify_artifacts_for`  | still names the gate whose approval is void (F5)      |
| `stage.stale`                   | the lossless remedy - never "record it again" (F5)    |
| `feedback.capture`              | NOT re-pinned as a person's edit (F5)                 |

Plus the probe's size cap (F6), and F4: the stub artifacts `slice.py` pins are LF.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import feedback as F  # noqa: E402
from tools.sdlc import slice as SL  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as S  # noqa: E402


@pytest.fixture
def run(tmp_path: Path) -> tuple[Path, str]:
    """A run whose P3 artifact was written and pinned by `slice.deliver`."""
    target = tmp_path / "repo"
    (target / ".sdlc").mkdir(parents=True)
    rid = S.create(target, "requirement", "a change worth making")["run_id"]
    SL.deliver(target, rid, "P3", ["api"])
    return target, rid


def _convert(target: Path, rid: str, rel: str = "30-design.md") -> Path:
    """What `core.autocrlf=true` does to a file on checkout."""
    path = S.run_dir(target, rid) / rel
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    return path


def test_the_stub_artifacts_slice_pins_are_written_in_lf_on_every_platform(run):
    """F4. `slice.py` wrote the artifacts it then pins with the platform newline,
    so a walk on Windows froze CRLF into a run folder that is now committed byte
    for byte, where a walk on Linux wrote LF. Red on Windows before the fix."""
    target, rid = run
    written = SL.deliver(target, rid, "P4", ["api", "web"]) + ["30-design.md"]
    for rel in written:
        path = S.run_dir(target, rid) / rel.rstrip("/")
        for f in ([path] if path.is_file() else sorted(path.rglob("*.md"))):
            assert b"\r" not in f.read_bytes(), f"{rel}: {f.name}"


def test_a_converted_file_is_its_own_kind_and_keeps_the_recorded_sentence(run):
    target, rid = run
    _convert(target, rid)
    (row,) = S.drift(target, rid, S.load(target, rid)["artifacts"])
    assert row[:2] == ("30-design.md", "converted"), row
    (said,) = S.verify_artifacts(target, rid)
    assert "changed since it was recorded" in said
    assert "only its line endings differ" in said and "git checkout" in said, said


def test_a_converted_file_still_names_the_gate_whose_approval_it_voids(run):
    """F5. The line-endings sentence REPLACED the gate's own, so the reader was no
    longer told which approval was void."""
    target, rid = run
    entry = {"verdict": "approve", "gate": "G2",
             "artifacts": dict(S.load(target, rid)["artifacts"])}
    _convert(target, rid)
    (said,) = A.verify_artifacts_for(target, rid, entry)
    assert "changed since G2 approved it" in said, said
    assert "only its line endings differ" in said, said


def test_stale_offers_the_lossless_remedy_and_never_record_it_again(run):
    """F5. `stale` printed the line-endings remedy AND `changed`'s own "record it
    again" in one sentence - and the second would re-pin the converted bytes and
    void the approval without saying so."""
    target, rid = run
    _convert(target, rid)
    (said,) = ST.stale(target, rid, "P3")
    assert "git checkout" in said, said
    assert "--record" not in said and "record it again" not in said, said


def test_capture_does_not_re_pin_a_conversion_as_a_persons_edit(run):
    """F5, and a pre-existing path: `pending_edits` treats `changed` as an edit
    and `repin` then approves it, so a Windows reviewer's gate silently pinned
    bytes a checkout had made. A conversion is reported as lost and left pinned."""
    target, rid = run
    pinned = S.load(target, rid)["artifacts"]["30-design.md"]
    _convert(target, rid)
    rows = F.capture(target, rid, "G2", "approve", "someone")
    kinds = {(r["kind"], r.get("problem")) for r in rows if r.get("artifact") == "30-design.md"}
    assert kinds == {("artifact-lost", "converted")}, kinds
    assert S.load(target, rid)["artifacts"]["30-design.md"] == pinned, "re-pinned"


def test_the_probe_is_capped_so_a_large_artifact_is_not_read_whole(run, monkeypatch):
    """F6. The probe reads the file and two converted copies; the digest streams
    in chunks on purpose. Past the cap the plain `changed` stands."""
    target, rid = run
    monkeypatch.setattr(S, "LINE_ENDING_PROBE_MAX", 16)
    path = _convert(target, rid)
    assert path.stat().st_size > 16
    (row,) = S.drift(target, rid, S.load(target, rid)["artifacts"])
    assert row == ("30-design.md", "changed", ""), row
