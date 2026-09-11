"""A lone `sdlc-init.py` must not teach commands it cannot supply.

`--bundle` emits ONE self-contained file. Nothing else travels with it: no
`tools/sdlc/stage.py`, no `sdlc/stages/`. The installer nevertheless rendered
`@@HARNESS@@` from `harness_root()` -- the directory the lone file happens to sit
in -- so the installed skill named `<that dir>/tools/sdlc/stage.py` and a runner
who pasted it got *can't open file*. The same run had ALREADY measured the
absence (`no_ledger_gate_reason` prints a NOTE about `tools/sdlc/validate.py`
missing from that very directory) and never composed that knowledge with the
renderer -- the producer/consumer-never-composed shape, at the one address this
repository's own machine-path guard cannot see, because that guard scans THIS
tree and the damage lands in somebody else's.

The control tests matter as much: a normal install still teaches every command it
CAN supply, or this trades one silent failure for another.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _load_init():
    spec = importlib.util.spec_from_file_location("sdlc_init", REPO / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sdlc_init", mod)
    spec.loader.exec_module(mod)
    return mod


INIT = _load_init()


def _bundle_into(d: Path) -> Path:
    """Emit a bundle as its own process, the way an adopter gets one."""
    out = d / "sdlc-init.py"
    proc = subprocess.run([sys.executable, str(REPO / "sdlc-init.py"), "--bundle", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert [p.name for p in d.iterdir()] == ["sdlc-init.py"], \
        "a bundle is ONE file; this fixture must not accidentally supply a tools/ tree"
    return out


def _target(d: Path) -> Path:
    root = d / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
    return root


def test_a_lone_bundle_installs_no_command_naming_a_file_that_is_not_there(tmp_path):
    """The row's own exit criterion, proven by spawning: install a lone bundle into
    an empty directory, then RUN what the skill teaches."""
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    bundle = _bundle_into(standalone)
    root = _target(tmp_path)

    proc = subprocess.run([sys.executable, str(bundle), "--target", str(root),
                           "--tools", "claude"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    skill = root / ".claude" / "skills" / "sdlc" / "SKILL.md"
    body = skill.read_text(encoding="utf-8")
    assert "@@HARNESS@@" not in body, "an unfilled placeholder shipped to an adopter"

    # Every path this document names under the bundle's own directory must exist.
    here = standalone.as_posix()
    named = [ln.strip() for ln in body.splitlines() if here in ln]
    assert not named, (
        "the skill names the directory the lone bundle sits in, which holds only "
        "sdlc-init.py:\n  " + "\n  ".join(named))


def test_a_lone_bundle_says_why_it_teaches_no_commands(tmp_path):
    """Omitting silently is the other half of the same defect: the run measured the
    absence, so it owes the adopter the reason."""
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    bundle = _bundle_into(standalone)
    root = _target(tmp_path)

    proc = subprocess.run([sys.executable, str(bundle), "--target", str(root),
                           "--tools", "claude"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    assert "no harness commands" in out, \
        "the run knew and said nothing:\n" + out

    body = (root / ".claude" / "skills" / "sdlc" / "SKILL.md").read_text(encoding="utf-8")
    assert "tools/sdlc/stage.py" not in body, \
        "the skill still teaches the one command this install cannot supply"
    assert "re-run" in body and "sdlc-init.py" in body, \
        "the skill must say how to GET the commands, not merely drop them"


def test_the_renderer_asks_the_same_question_the_ledger_note_asks(tmp_path):
    """Composed, not re-derived: both the NOTE and the skill decide from the presence
    of real files beside the script, so one cannot say `absent` while the other
    writes a path into it."""
    assert INIT.no_harness_commands_reason(tmp_path) is not None
    assert INIT.no_ledger_gate_reason(tmp_path, tmp_path) is not None
    # And a real harness clone answers None to both halves of the question.
    assert INIT.no_harness_commands_reason(REPO) is None


@pytest.mark.parametrize("missing", ["tools/sdlc/stage.py", "sdlc/stages"])
def test_either_absent_source_alone_is_enough_to_omit(tmp_path, missing):
    """Half a harness is not a harness: a tree with one of the two sources still
    cannot supply the commands that need the other."""
    for rel in ("tools/sdlc/stage.py", "sdlc/stages"):
        if rel == missing:
            continue
        p = tmp_path / rel
        if rel.endswith(".py"):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", encoding="utf-8")
        else:
            p.mkdir(parents=True, exist_ok=True)
    reason = INIT.no_harness_commands_reason(tmp_path)
    assert reason is not None and missing in reason, reason


# ── the control direction: a real clone must still teach what it CAN supply ──

def test_a_normal_install_still_teaches_the_commands_it_can_supply(tmp_path):
    """The inverse failure. If the omission fires against a real harness clone the
    skill goes mute, and a mute skill fails exactly as silently."""
    root = _target(tmp_path)
    proc = subprocess.run([sys.executable, str(REPO / "sdlc-init.py"), "--target", str(root),
                           "--tools", "claude"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = (root / ".claude" / "skills" / "sdlc" / "SKILL.md").read_text(encoding="utf-8")
    assert "@@HARNESS@@" not in body
    assert "tools/sdlc/stage.py" in body, "a real clone stopped teaching its own commands"
    assert "no harness commands" not in body
    # And what it teaches resolves: the prefix names a file that is there.
    taught = [ln for ln in body.splitlines() if "tools/sdlc/stage.py" in ln]
    assert taught
    for ln in taught:
        start = ln.index('python "') + len('python "') if 'python "' in ln else None
        if start is None:
            continue
        path = Path(ln[start:ln.index('"', start)])
        assert path.is_file(), f"a normal install teaches an absent file: {path}"
