"""T450c: the installer draws `.sdlc/pipeline.md`, last, from the pipeline the
install actually left.

The diagram is drawn by SPAWNING `stage.py --diagram mermaid` - the installer may
not import `tools/` - so every claim here is about the installer as a process or
about the helper that spawns, never a re-implementation of the renderer.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import stage  # noqa: E402
from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"


def _installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_diagram", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _target(tmp_path: Path) -> Path:
    root = tmp_path / "target"
    root.mkdir()
    _project.seed(root)
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    return root


def _install(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALLER), "--target", str(root),
                           "--no-graph", *extra], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)


def test_install_draws_the_pipeline_it_resolved(tmp_path):
    """A real install leaves `.sdlc/pipeline.md` equal to a fresh render of the
    profile it wrote, reports it in the file list's vocabulary, and a re-run
    reports it unchanged."""
    root = _target(tmp_path)
    done = _install(root)
    assert done.returncode == 0, done.stdout + done.stderr
    diagram = root / ".sdlc" / "pipeline.md"
    assert diagram.is_file(), done.stdout
    assert diagram.read_text(encoding="utf-8") == stage.diagram_for(root)
    assert "+ create" in done.stdout.split("pipeline diagram:", 1)[1], done.stdout

    again = _install(root)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "= unchanged" in again.stdout.split("pipeline diagram:", 1)[1], again.stdout


def test_the_installed_diagram_passes_its_own_drift_check(tmp_path):
    root = _target(tmp_path)
    assert _install(root).returncode == 0
    check = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
                            "--target", str(root), "--diagram", "mermaid", "--check"],
                           capture_output=True, text=True, timeout=300)
    assert check.returncode == 0, check.stdout


def test_dry_run_says_it_would_draw_and_writes_nothing(tmp_path):
    root = _target(tmp_path)
    done = _install(root, "--dry-run")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[dry-run] pipeline: would draw .sdlc/pipeline.md" in done.stdout, done.stdout
    assert not (root / ".sdlc" / "pipeline.md").exists()


def test_a_standalone_installer_skips_with_its_reason(tmp_path):
    """Copied out of its clone there is no `stage.py` to draw with: said, not
    silently absent, and nothing written."""
    si = _installer()
    root = _target(tmp_path)
    (root / ".sdlc").mkdir()
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    assert "standalone sdlc-init.py" in si.no_pipeline_diagram_reason(lonely)
    line = si.write_pipeline_diagram(root, harness=lonely)
    assert "- skipped" in line and "standalone" in line, line
    assert not (root / ".sdlc" / "pipeline.md").exists()


def test_a_renderer_that_fails_is_reported_blocked_with_its_words(tmp_path):
    """A renderer that exits non-zero is BLOCKED with its last line, never a
    `+ create` over a file that was not written."""
    si = _installer()
    root = _target(tmp_path)
    (root / ".sdlc").mkdir()
    fake = tmp_path / "harness"
    (fake / "tools" / "sdlc").mkdir(parents=True)
    (fake / "tools" / "sdlc" / "stage.py").write_text(
        "print('!! phases[] names a step that does not exist')\nraise SystemExit(2)\n",
        encoding="utf-8")
    line = si.write_pipeline_diagram(root, harness=fake)
    assert "x BLOCKED (diagram could not be drawn)" in line, line
    assert "exited 2" in line and "does not exist" in line, line


def test_a_diagram_it_can_write_but_not_read_back_is_said_not_a_traceback(tmp_path):
    """T568 (batch-6 review, suspected; the owner's ruling: reproduce it with the real
    trigger). The renderer writes the diagram IN PLACE, so a diagram file that can be
    written but not read stays that way - and the installer read it back unguarded,
    a traceback at exit 1 after the install had written everything else (T517's
    class). Whether the file really is unreadable is measured, not assumed from the
    platform: permission bits make it so on Linux as CI runs it; on Windows, which
    cannot make a file write-only that way, and for root, the read succeeds and the
    ordinary line is asserted instead."""
    root = _target(tmp_path)
    assert _install(root).returncode == 0
    diagram = root / ".sdlc" / "pipeline.md"
    diagram.chmod(0o200)
    try:
        try:
            diagram.read_bytes()
            unreadable = False
        except PermissionError:
            unreadable = True
        again = _install(root)
    finally:
        diagram.chmod(0o644)
    assert again.returncode == 0 and "Traceback" not in again.stderr, \
        again.stdout + again.stderr
    line = again.stdout.split("pipeline diagram:", 1)[1].splitlines()[1]
    if unreadable:
        assert "could not be read back" in line and str(root) not in line, line
    else:
        assert "= unchanged" in line, line
