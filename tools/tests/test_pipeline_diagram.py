"""T450a: the pipeline, drawn — generated from the resolved pipeline, never by hand.

`stage.py --list` gains a terminal diagram above its rows, and `--diagram mermaid`
writes a committed, deterministic `.sdlc/pipeline.md` that `--check` holds to the
profile. Every claim a PERSON sees is tested by spawning the CLI (a CLI's
contract is a process), and the renderers are pinned as pure functions too,
because the wizard (T371) will call them on a pipeline no profile describes yet.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import stage  # noqa: E402

STAGE = ROOT / "tools" / "sdlc" / "stage.py"


def _profile(root: Path, body: str = "") -> Path:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")
    return root


def _spawn(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STAGE), *args], capture_output=True,
                          timeout=300, cwd=str(ROOT), env=env)


def _text(target: Path) -> list[str]:
    return stage.render_text(stage.pipeline(target),
                             set(stage.resolved_hard_gates(target)))


def test_default_pipeline_diagram_is_pinned(tmp_path):
    """The golden picture of the solo default on a workspace with no graph and no
    deploy: G1 and G5 dropped (capability), G2 hard, G4 the merge gate."""
    assert _text(_profile(tmp_path)) == [
        "P0 intake --> P1 requirements --> P3 design ==[G2 hard]==> P4 implementation",
        "   +==[G3 advisory]==> P5 test --> P6 review --> P7 triage",
        "   +==[G4 merge]==> P11 closeout",
        "   skipped: scoping, release, deploy, verify (not listed in phases[])",
        "   dropped: G1, G5",
    ]


def test_deleting_a_step_and_adding_a_gate_changes_the_picture(tmp_path):
    """Delete `design` and add `extra_gates: [test]`: G2 is drawn at its NEW step
    and says where it moved from, and `G-test` stands after `test`."""
    target = _profile(tmp_path, "phases: [intake, requirements, implementation, "
                                "test, review, triage, closeout]\nextra_gates: [test]\n")
    text = "\n".join(_text(target))
    assert "P1 requirements ==[G2 hard]==> P4 implementation" in text, text
    assert "P5 test ==[G-test hard]==> P6 review" in text, text
    assert "moved: G2 from P3 design to P1 requirements" in text, text
    assert "P3 design" not in text.split("skipped:")[0], text


def test_every_line_fits_an_80_column_console(tmp_path):
    target = _profile(tmp_path, "extra_gates: [intake, requirements, test, review]\n")
    notes = ("   skipped:", "   moved:", "   dropped:")
    arrows = [ln for ln in _text(target) if not ln.startswith(notes)]
    assert len(arrows) > 1, "the case must actually wrap"
    for line in arrows:
        assert len(line) <= stage.DIAGRAM_WIDTH, line


def test_mermaid_render_is_deterministic_and_carries_no_path(tmp_path):
    """Two renders are byte-identical, and two workspaces with the same profile
    render the same bytes — nothing about WHERE it ran reaches the file."""
    body = "extra_gates: [test]\n"
    a = _profile(tmp_path / "one", body)
    b = _profile(tmp_path / "two dir", body)
    assert stage.diagram_for(a) == stage.diagram_for(a)
    assert stage.diagram_for(a) == stage.diagram_for(b)
    assert str(tmp_path) not in stage.diagram_for(a)


def test_mermaid_names_every_step_and_every_gate(tmp_path):
    target = _profile(tmp_path, "extra_gates: [test]\n")
    md = stage.diagram_for(target)
    assert "```mermaid\nflowchart LR\n" in md
    for s in stage.pipeline(target).steps:
        assert f'{s.phase}["{s.phase} {s.name}"]' in md, s
    assert 'G_test{{"G-test hard"}}' in md and "P5 --> G_test --> P6" in md, md
    assert "| P5 | test | 50-tests.md | G-test hard |" in md, md
    assert "- G5: G5 stands at the end of P9 (deploy)" in md, md


def test_diagram_writes_then_check_passes_then_catches_drift(tmp_path):
    """The drift guard as a person meets it: write, check clean (exit 0), change
    the profile, check stale (exit 1) naming the command that regenerates it."""
    target = _profile(tmp_path)
    done = _spawn("--target", str(target), "--diagram", "mermaid")
    assert done.returncode == 0, done.stdout
    written = (target / ".sdlc" / "pipeline.md").read_bytes()
    assert written == stage.diagram_for(target).encode("utf-8")
    assert b"\r\n" not in written, "LF on every OS, so the committed bytes agree"

    done = _spawn("--target", str(target), "--diagram", "mermaid", "--check")
    assert done.returncode == 0, done.stdout

    _profile(target, "extra_gates: [test]\n")
    done = _spawn("--target", str(target), "--diagram", "mermaid", "--check")
    assert done.returncode == 1, done.stdout
    assert b"stale" in done.stdout and b"--diagram mermaid" in done.stdout, done.stdout
    assert (target / ".sdlc" / "pipeline.md").read_bytes() == written, \
        "--check must never write"


def test_check_accepts_a_crlf_checkout(tmp_path):
    """git's autocrlf on Windows checks the file out with CRLF; that is not drift."""
    target = _profile(tmp_path)
    (target / ".sdlc" / "pipeline.md").write_bytes(
        stage.diagram_for(target).replace("\n", "\r\n").encode("utf-8"))
    done = _spawn("--target", str(target), "--diagram", "mermaid", "--check")
    assert done.returncode == 0, done.stdout


def test_check_on_a_missing_file_fails_by_name(tmp_path):
    done = _spawn("--target", str(_profile(tmp_path)), "--diagram", "mermaid", "--check")
    assert done.returncode == 1, done.stdout
    assert b"missing" in done.stdout, done.stdout


def test_diagram_refuses_a_target_with_no_install(tmp_path):
    """No `.sdlc/` means no install: refuse rather than create one."""
    done = _spawn("--target", str(tmp_path), "--diagram", "mermaid")
    assert done.returncode == 2, done.stdout
    assert b"no .sdlc/" in done.stdout, done.stdout
    assert not (tmp_path / ".sdlc").exists()


def test_check_without_diagram_is_refused(tmp_path):
    done = _spawn("--target", str(_profile(tmp_path)), "--check")
    assert done.returncode == 2, done.stdout
    assert b"--check needs --diagram" in done.stdout, done.stdout


def test_list_diagram_reads_under_any_decoder(tmp_path):
    """**Measured, and why the diagram is ASCII.** The first cut drew `═`, whose
    UTF-8 bytes carry `0x90` — undefined in cp1252 — so a Windows caller
    spawning `--list` with Python's DEFAULT decoding (`text=True`, the locale
    code page) got `''` at exit 0: the reader thread raised and the whole
    output, rows included, vanished. Spawned exactly that way here."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    done = subprocess.run([sys.executable, str(STAGE), "--target",
                           str(_profile(tmp_path)), "--list"],
                          capture_output=True, text=True, timeout=300,
                          cwd=str(ROOT), env=env)
    assert done.returncode == 0, done.stderr
    out = done.stdout or ""
    assert "P0 intake --> P1 requirements" in out, (out, done.stderr)
    assert "==[G4 merge]==>" in out, out
    assert out.index("P0 intake -->") < out.index("phases from"), (
        "the picture sits above the rows")
    diagram = out.split("phases from", 1)[0]
    assert diagram.isascii(), [c for c in diagram if not c.isascii()]


def test_list_rows_name_the_gate_where_it_stands(tmp_path):
    """A row's `-> G` names the gate that STANDS at that step: a moved G2 is
    shown on requirements, not on the deleted design."""
    target = _profile(tmp_path, "phases: [intake, requirements, implementation, "
                                "test, review, triage, closeout]\nextra_gates: [test]\n")
    out = _spawn("--target", str(target), "--list").stdout.decode("utf-8")
    rows = out.split("phases from", 1)[1]
    assert "requirements    10-requirements.md  -> G2" in rows, rows
    assert "test            50-tests.md  -> G-test" in rows, rows
