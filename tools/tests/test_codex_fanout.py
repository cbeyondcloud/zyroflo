"""T449: Codex gets the `/sdlc` skill, through AGENTS.md.

`detect_tools` knew six tools and not Codex, so a Codex user got no entry point.
Codex reads AGENTS.md - which is also the cross-tool instruction file the
pointer and graph sections live in, and a file holds exactly ONE harness block.
So the skill is COMPOSED into that block, first, rather than planned as a second
op on the same file (which `surface_ops` refuses: the second write would drop
the first's block).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"


def _si():
    spec = importlib.util.spec_from_file_location("sdlc_init_codex", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SI = _si()


def _target(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    return _project.seed(root)


def _install(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALLER), "--target", str(root),
                           "--no-graph", *extra], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)


@pytest.mark.parametrize("marker", ["AGENTS.md", ".codex"])
def test_codex_is_detected_by_its_files(tmp_path, marker):
    (tmp_path / marker).mkdir() if marker == ".codex" else \
        (tmp_path / marker).write_text("# notes\n", encoding="utf-8")
    assert "codex" in SI.detect_tools(tmp_path)


def test_codex_is_not_detected_without_them(tmp_path):
    assert "codex" not in SI.detect_tools(tmp_path)


def test_the_skill_lands_in_one_agents_block_with_the_users_text_kept(tmp_path):
    """The adopter's own AGENTS.md text survives outside the markers; inside,
    exactly one harness block carrying the skill."""
    root = _target(tmp_path)
    (root / "AGENTS.md").write_text("# Our agent notes\n\nBe kind to the CI.\n",
                                    encoding="utf-8")
    done = _install(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "claude, codex" in done.stdout, done.stdout
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(SI.BLOCK_BEGIN) == 1 and text.count(SI.BLOCK_END) == 1
    assert "Be kind to the CI." in text
    inside = text.split(SI.BLOCK_BEGIN, 1)[1].split(SI.BLOCK_END, 1)[0]
    assert "# SDLC Harness" in inside, "the skill itself, not only a pointer"


def test_a_rerun_leaves_the_block_unchanged(tmp_path):
    root = _target(tmp_path)
    assert _install(root, "--tools", "claude,codex").returncode == 0
    before = (root / "AGENTS.md").read_bytes()
    again = _install(root, "--tools", "claude,codex")
    assert again.returncode == 0
    assert "= unchanged" in next(l for l in again.stdout.splitlines() if "AGENTS.md" in l)
    assert (root / "AGENTS.md").read_bytes() == before


def test_the_skill_comes_first_and_the_pointer_is_still_carried(tmp_path):
    """Composed, not overwritten: with --claude-extras the pointer section is
    in the same block, AFTER the skill, so `block_parts` still finds it."""
    root = _target(tmp_path)
    done = _install(root, "--tools", "claude,codex", "--claude-extras")
    assert done.returncode == 0, done.stdout + done.stderr
    inside = (root / "AGENTS.md").read_text(encoding="utf-8").split(SI.BLOCK_BEGIN, 1)[1]
    assert inside.index("# SDLC Harness") < inside.index(SI.POINTER_HEADING)
    assert SI.POINTER_HEADING in SI.block_parts(root, "AGENTS.md")


def test_codex_with_a_graph_carries_the_graph_skill_in_the_same_block(tmp_path,
                                                                      monkeypatch):
    """Codex is a block tool, so the graph tier joins its one block - planned
    in-process with the graph gate forced on, one op for AGENTS.md."""
    root = _target(tmp_path)
    ops = SI.surface_ops(root, ["codex"], "SKILL BODY", "desc", with_graph=True,
                         extras_on=False)
    agents = [o for o in ops if o["rel"] == "AGENTS.md"]
    assert len(agents) == 1, [o["rel"] for o in ops]
    content = agents[0]["content"]
    assert "SKILL BODY" in content and SI.GRAPH_HEADING in content
