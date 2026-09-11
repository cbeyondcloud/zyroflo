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
import re
import subprocess
import sys
from pathlib import Path

import pytest

#: What a taught harness command LOOKS like, wherever it appears -- prose, a backticked
#: fragment, or a fenced block. The completeness guard below is keyed on this rather than
#: on a list of the paths anyone happens to have thought of, because the list is what
#: failed: the first pass at this item removed one section and left seven lines teaching
#: `stage.py`, `state.py` and `review/loop.py`.
TAUGHT_COMMAND = re.compile(r"tools/[\w./-]*\.py")

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


def _every_taught_command_runs(body: str, harness: Path) -> list[str]:
    """SPAWN each harness-rooted command the installed skill teaches; return them.

    The row's exit criterion is not "the document looks right", it is that a runner who
    pastes what it says gets a program. So each is invoked as its own process and the
    exact failure the defect produced -- *can't open file*, rc 2 -- is asserted absent.
    """
    taught = sorted(set(TAUGHT_COMMAND.findall(body)))
    for rel in taught:
        target = harness / rel
        assert target.is_file(), f"the skill teaches an absent file: {target}"
        proc = subprocess.run([sys.executable, str(target), "--help"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(harness))
        assert "can't open file" not in (proc.stdout + proc.stderr), \
            f"{rel} is what a runner pastes and it does not open:\n{proc.stderr}"
        assert proc.returncode != 2, f"{rel} exited 2:\n{proc.stderr}"
    return taught


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

    # ...and RUN what it teaches. The bundle teaches no program, so the runnable set is
    # empty; _every_taught_command_runs is the same check applied to the normal install
    # below, where it is NOT empty, which is what keeps this half from being vacuous.
    assert _every_taught_command_runs(body, standalone) == []


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
    left = sorted(set(TAUGHT_COMMAND.findall(body)))
    assert not left, \
        "the skill still teaches commands this install cannot supply: " + ", ".join(left)
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


# ── derived, not listed: the template declares its own harness-dependent regions ──

TEMPLATE = REPO / "sdlc_templates" / "SKILL.md"


def test_the_template_leaves_no_taught_command_outside_a_marked_region():
    """The completeness guard, and the answer to 'what about the eighth command'.

    Nothing here enumerates which lines to strip. It runs the real resolver over the
    real template with the harness ABSENT and asks whether any harness-rooted command
    survived. A command added tomorrow outside a `<!--harness-only-->` region reddens
    this test in the harness's own tree, where the damage is not.
    """
    tpl = TEMPLATE.read_text(encoding="utf-8")
    # Positive control: the guard is worthless if the template teaches nothing.
    taught = sorted(set(TAUGHT_COMMAND.findall(tpl)))
    assert len(taught) >= 3, f"template teaches too little to be guarding: {taught}"

    standalone = INIT.resolve_skill_regions(tpl, harness_present=False)
    escaped = sorted(set(TAUGHT_COMMAND.findall(standalone)))
    assert not escaped, (
        "these taught commands live OUTSIDE a <!--harness-only--> region, so a lone "
        "bundle would teach them: " + ", ".join(escaped))
    assert "@@HARNESS@@" not in standalone, \
        "a harness placeholder lives outside a region and would be filled with a path"

    # And the harness branch is not empty either -- a region whose only branch is the
    # standalone one would pass the line above while gutting a real clone's skill.
    kept = sorted(set(TAUGHT_COMMAND.findall(
        INIT.resolve_skill_regions(tpl, harness_present=True))))
    assert kept == taught, f"the harness branch lost commands the template teaches: {taught}"


@pytest.mark.parametrize("marker", ["<!--harness-only-->", "<!--else-->",
                                    "<!--/harness-only-->"])
def test_no_region_marker_survives_either_rendering(tmp_path, marker):
    """A marker shipped literal into an adopter's skill is the same class of defect as
    a placeholder shipped literal. Both directions, since only one is exercised by the
    install tests above."""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    assert marker in tpl, "the template stopped using the marker this pins"
    for present in (True, False):
        assert marker not in INIT.resolve_skill_regions(tpl, harness_present=present)


@pytest.mark.parametrize("broken,why", [
    ("a\n<!--harness-only-->\nb\n", "unclosed"),
    ("a\n<!--/harness-only-->\n", "unopened"),
    ("a\n<!--else-->\nb\n", "<!--else--> outside"),
    ("<!--harness-only-->\n<!--harness-only-->\nb\n<!--/harness-only-->\n", "nested"),
])
def test_a_malformed_region_is_refused_loudly(broken, why):
    """The positive control for the resolver's own guard: a template that grew a broken
    region must raise HERE and not ship its markers to an adopter."""
    with pytest.raises(ValueError):
        INIT.resolve_skill_regions(broken, harness_present=False)
    with pytest.raises(ValueError):
        INIT.resolve_skill_regions(broken, harness_present=True)


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
    # And what it teaches RUNS -- spawned, the same helper the bundle half calls, here
    # against a non-empty set. Three or more, or the omission has quietly gutted a real
    # clone's skill and this control would still be green.
    ran = _every_taught_command_runs(body, REPO)
    assert len(ran) >= 3, f"a real clone stopped teaching its own commands: {ran}"
    assert "tools/sdlc/stage.py" in ran
    # The prefix is still there and still names a file that is there.
    prefixed = [ln for ln in body.splitlines()
                if 'python "' in ln and "tools/sdlc/stage.py" in ln]
    assert prefixed, "the harness-rooted prefix stopped being taught"
    for ln in prefixed:
        start = ln.index('python "') + len('python "')
        assert Path(ln[start:ln.index('"', start)]).is_file()
