"""The drop-in install writes ALL SIX tool fan-outs, for real. Item 67.

M6's C5 asks that the drop-in path is verified unbroken: a fresh-repo install, all
six tool fan-outs written, `--bundle` single-file mode intact, and an existing
`.sdlc/` upgraded without clobbering user edits. Three of those four are covered by
`test_repo_contract.py`; **this file is the fourth**.

**Measured before it was written: `ALL_TOOLS` appeared in exactly ONE test in the
whole suite**, and that one exercises the graph tier through a helper. No test
anywhere ran a real install with all six tools and checked that each surface landed
— so five of the six fan-outs were shipped on the strength of a table nobody
executed.

**These tests SPAWN the installer** rather than importing and calling it. Item 96's
rule, and it earned itself there: every defect the first field run found lived at or
below the process boundary, and a driver that imported the functions would have
reproduced each one faithfully and reported them all green.

**The expectation is FROZEN here, not derived from the code under test — and the
first cut of this file got that wrong.** Deriving `surfaces_for(tool)` from
`tool_files` looked like the right way to avoid a second spelling of the contract.
It is not: when a mutation made `tool_files("gemini")` return `[]`, the derivation
returned `[]` too, the assertion loop never ran, and the test passed. **Six of eight
mutations survived** — a tool losing its surface entirely, another writing to the
wrong path, a block silently becoming a file.

So the paths are written down, the way `EXPECTED.md` and `core_baseline.md` are
written down: a frozen table a person must edit deliberately. That IS a second
spelling, and being a second spelling is the point — a contract test that reads its
expectation from the implementation is a mirror, not a test.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_fanout", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SI = load_installer()


def install(target: Path, *extra: str) -> subprocess.CompletedProcess:
    """A REAL install, spawned. `--no-graph` because this file is about the
    fan-out and a chained build would make a graph-layer failure present as a
    fan-out defect."""
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(target)], check=True,
                   capture_output=True, timeout=120)
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target), "--no-graph",
         *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    return proc


#: **The contract, frozen.** Every surface the drop-in install must write, per
#: tool, with its kind. Written down rather than derived from `tool_files`, because
#: a test that reads its expectation from the implementation agrees with any change
#: to it — measured: deriving this let SIX of eight mutations through, including a
#: tool losing its surface entirely and a block silently becoming a file.
#:
#: `file` gets its own path per skill; `block` writes into a file the adopter also
#: owns, which is why one-block-per-file is a law rather than a preference.
EXPECTED: dict[str, list[tuple[str, str]]] = {
    "claude":   [(".claude/skills/sdlc/SKILL.md", "file")],
    "cursor":   [(".cursor/rules/sdlc.mdc", "file")],
    "windsurf": [(".windsurf/rules/sdlc.md", "file")],
    "copilot":  [(".github/copilot-instructions.md", "block")],
    "gemini":   [("GEMINI.md", "block")],
    "aider":    [("CONVENTIONS.md", "block")],
}


def surfaces_for(tool: str) -> list[tuple[str, str]]:
    """What this tool must produce, from the frozen table above."""
    return EXPECTED[tool]


def test_the_frozen_table_matches_what_the_installer_actually_offers():
    """The frozen table's one risk is going stale, so it is checked against the
    implementation ONCE, here — and every other test in this file reads the table
    rather than the implementation. A tool added or a path changed fails this test
    by name, which is a person's decision to record rather than a silent drift."""
    for tool in SI.ALL_TOOLS:
        actual = [(rel, kind) for rel, _b, kind in SI.tool_files(tool, "body", "desc")]
        assert actual == EXPECTED[tool], f"{tool}: the fan-out changed"
    assert set(EXPECTED) == set(SI.ALL_TOOLS), "the table and ALL_TOOLS disagree"


def test_every_tool_contributes_at_least_one_surface():
    """The guard the first cut of this file lacked. Without it, a tool that
    produces nothing makes every loop below iterate zero times and assert
    nothing — which is how six mutations survived."""
    for tool in SI.ALL_TOOLS:
        assert surfaces_for(tool), f"{tool} contributes no surface"
    total = sum(len(v) for v in EXPECTED.values())
    assert total == 6, f"expected six surfaces across six tools, found {total}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
    return tmp_path


ALL = "claude,cursor,windsurf,copilot,gemini,aider"


# ── the fan-out, for real ───────────────────────────────────────────────────


def test_the_installer_offers_exactly_six_tools():
    """The count is asserted by hand on purpose: everything else here derives from
    `ALL_TOOLS`, and a derivation over an emptied list would agree with an
    installer that wrote nothing."""
    assert SI.ALL_TOOLS == ["claude", "cursor", "windsurf", "copilot", "gemini",
                            "aider"]


def test_a_real_install_writes_every_tools_surface(repo):
    """**The gap this file exists for.** Five of the six fan-outs had never been
    through a real install in any test."""
    proc = install(repo, "--tools", ALL)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    missing = []
    for tool in SI.ALL_TOOLS:
        for rel, _kind in surfaces_for(tool):
            if not (repo / rel).is_file():
                missing.append(f"{tool} -> {rel}")
    assert not missing, "surfaces a real install did not write: " + ", ".join(missing)


def test_every_written_surface_carries_the_skill_and_not_a_placeholder(repo):
    """A file that exists and says `@@SOMETHING@@` is worse than one that is
    absent: the absence is visible, and the placeholder is read as instruction."""
    assert install(repo, "--tools", ALL).returncode == 0
    for tool in SI.ALL_TOOLS:
        for rel, _kind in surfaces_for(tool):
            body = (repo / rel).read_text(encoding="utf-8")
            assert body.strip(), f"{rel} is empty"
            assert "@@" not in body, f"{rel} still carries a template placeholder"


def test_a_block_kind_tool_gets_ONE_block_however_often_it_is_installed(repo):
    """`merge_block`'s one-block law. Three of the six write into a file the
    adopter also owns, and a second block is how a re-run starts appending
    forever."""
    assert install(repo, "--tools", ALL).returncode == 0
    first = {rel: (repo / rel).read_text(encoding="utf-8")
             for tool in SI.ALL_TOOLS for rel, kind in surfaces_for(tool)
             if kind == "block"}
    assert first, "no block-kind tool in the fan-out - the derivation found nothing"

    assert install(repo, "--tools", ALL).returncode == 0
    for rel, before in first.items():
        after = (repo / rel).read_text(encoding="utf-8")
        assert after == before, f"{rel} changed on a plain re-install"


def test_a_reinstall_does_not_clobber_text_the_adopter_wrote(repo):
    """C5's fourth part, applied to every block-kind surface rather than to one.
    An adopter's own prose around the harness block is theirs."""
    assert install(repo, "--tools", ALL).returncode == 0
    blocks = [rel for tool in SI.ALL_TOOLS for rel, kind in surfaces_for(tool)
              if kind == "block"]
    for rel in blocks:
        path = repo / rel
        path.write_text("# my own notes\n\n" + path.read_text(encoding="utf-8")
                        + "\n\ntrailing words of mine\n", encoding="utf-8")

    assert install(repo, "--tools", ALL).returncode == 0
    for rel in blocks:
        body = (repo / rel).read_text(encoding="utf-8")
        assert "# my own notes" in body, f"{rel} lost the adopter's leading text"
        assert "trailing words of mine" in body, f"{rel} lost the adopter's trailing text"


def test_selecting_one_tool_writes_only_that_tools_surface(repo):
    """The fan-out is a selection, not a broadcast. A `--tools claude` install that
    also wrote `GEMINI.md` would be putting files in repositories that never asked
    for them."""
    assert install(repo, "--tools", "claude").returncode == 0
    for rel, _kind in surfaces_for("claude"):
        assert (repo / rel).is_file()
    for tool in SI.ALL_TOOLS:
        if tool == "claude":
            continue
        for rel, kind in surfaces_for(tool):
            if kind == "block" and (repo / rel).exists():
                # A block-kind path may pre-exist for other reasons; what must not
                # happen is the harness writing its block into it.
                assert "sdlc" not in (repo / rel).read_text(encoding="utf-8").lower(), \
                    f"{rel} carries an sdlc block for a tool nobody selected"
            elif kind == "file":
                assert not (repo / rel).exists(), \
                    f"{rel} was written for a tool nobody selected"


# ── the bundle writes the same surfaces ─────────────────────────────────────


def test_the_single_file_bundle_writes_the_same_fan_out(repo, tmp_path):
    """`--bundle` is the zero-dependency drop-in path: one file, no harness clone.
    A bundle that installed a smaller fan-out would be a second installer, and the
    adopter who most needs the bundle is the one least able to notice."""
    bundle = tmp_path / "sdlc-init-bundled.py"
    made = subprocess.run(
        [sys.executable, str(INSTALLER), "--bundle", str(bundle)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    assert made.returncode == 0 and bundle.is_file(), f"{made.stdout}\n{made.stderr}"

    target = tmp_path / "viabundle"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True,
                   capture_output=True, timeout=120)
    ran = subprocess.run(
        [sys.executable, str(bundle), "--target", str(target), "--no-graph",
         "--tools", ALL],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"

    missing = [f"{tool} -> {rel}"
               for tool in SI.ALL_TOOLS for rel, _k in surfaces_for(tool)
               if not (target / rel).is_file()]
    assert not missing, "the bundle wrote a smaller fan-out: " + ", ".join(missing)
