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
import re
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


# ── the STANDALONE body reaches every one of the six frozen surfaces ─────────
#
# The tests above assert the six surfaces are WRITTEN. W170 measured a further claim
# nothing here asserted: a STANDALONE render (a lone bundle, no tools/ beside it) lands
# on all six carrying taught=0, markers=0, placeholder=0 -- true "by construction"
# because render_skill_body is called ONCE, above the fan-out, so every surface carries
# the one decision. Item 67's point is that a claim held on trust is the one a seventh
# surface breaks silently; these two directions hold it against the frozen roster. The
# existing bundle test checks only that each surface file EXISTS -- an unresolved body
# (markers, or a filled machine path) on a non-claude surface passes it and fails here.

_TAUGHT = re.compile(r"tools/[\w./-]*\.py")
_MARKERS = (SI.SKILL_REGION_OPEN, SI.SKILL_REGION_ELSE, SI.SKILL_REGION_CLOSE)


def test_the_standalone_body_reaches_every_one_of_the_six_surfaces(tmp_path):
    """A lone bundle is the standalone case. Every surface in the frozen roster must
    carry the resolved standalone body: no harness-rooted command, no region marker, no
    unfilled @@HARNESS@@ -- for ALL SIX, not the one claude surface the bundle-path tests
    already cover. Spawned, because the render happens across a process boundary."""
    bundle = tmp_path / "sdlc-init-bundled.py"
    made = subprocess.run(
        [sys.executable, str(INSTALLER), "--bundle", str(bundle)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert made.returncode == 0 and bundle.is_file(), f"{made.stdout}\n{made.stderr}"

    target = tmp_path / "standalone-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True,
                   capture_output=True, timeout=120)
    ran = subprocess.run(
        [sys.executable, str(bundle), "--target", str(target), "--no-graph",
         "--tools", ALL],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"
    assert "no harness commands" in (ran.stdout + ran.stderr), \
        "the standalone run must SAY it teaches no harness commands:\n" + ran.stdout + ran.stderr

    for tool in SI.ALL_TOOLS:
        for rel, _kind in surfaces_for(tool):
            body = (target / rel).read_text(encoding="utf-8")
            taught = sorted(set(_TAUGHT.findall(body)))
            assert not taught, f"{rel}: a standalone surface teaches absent commands {taught}"
            for m in _MARKERS:
                assert m not in body, f"{rel}: the region marker {m} shipped literal"
            assert "@@HARNESS@@" not in body, f"{rel}: an unfilled placeholder shipped"


def test_a_harness_present_install_DOES_teach_commands_on_every_surface(repo):
    """The control that keeps the test above from being vacuous: with a real harness the
    SAME six surfaces DO carry harness-rooted commands. So 'taught==0 everywhere' in the
    standalone case is the single render deciding once, not an assertion trivially true
    for any install."""
    assert install(repo, "--tools", ALL).returncode == 0
    for tool in SI.ALL_TOOLS:
        for rel, _kind in surfaces_for(tool):
            body = (repo / rel).read_text(encoding="utf-8")
            assert _TAUGHT.search(body), \
                f"{rel}: a real harness install taught no command -- the control is broken"


# ── T421: the bundle embeds every template load_template() can be asked for ──
#
# Two templates are loaded ON DEMAND -- snapshot-publish.yml (--snapshot-cadence)
# and corpus-replay.yml (--corpus-replay), from snapshot_job_ops / replay_job_ops --
# and sat in NEITHER TEMPLATE_FILES nor EXTRA_TEMPLATE_FILES. So `--bundle` (and
# `--dump-templates`) shipped 11 of the 13 and either flag died at the adopter's
# first use with `template '<name>' not found`. The real installer never failed:
# it reads the sibling sdlc_templates/ dir; only the bundle relies on _EMBEDDED.
#
# The fix DERIVES the bundled set from load_template()'s own call sites, so it
# cannot drift again (a new load_template("x") is embedded because it is LOADED, not
# because someone listed it), and a call it cannot resolve fails at BUILD time here.
# These tests SPAWN the bundle with each flag (the contract is a process).


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True,
                   capture_output=True, timeout=120)


def _harness_with_origin(base: Path) -> Path:
    """A git clone with a github origin: harness_repo_slug() reads the slug off it,
    and for a bundle harness_root() is the bundle's OWN dir, so the cadence jobs
    reach load_template() rather than the no-origin refusal."""
    h = base / "harnessclone"
    h.mkdir()
    _git(h.parent, "init", "-q", str(h))
    _git(h, "remote", "add", "origin", "https://github.com/an-owner/a-harness.git")
    return h


def _cadence_target(base: Path) -> Path:
    """A target the cadence jobs will install into: a repo with .github/, a
    workspace file at its top (root='.'), and one discoverable component."""
    t = base / "target"
    (t / ".github").mkdir(parents=True)
    (t / ".git").mkdir()
    (t / "comp-a" / ".git").mkdir(parents=True)
    (t / "sdlc-workspace.toml").write_text("root = '.'\n", encoding="utf-8")
    return t


def _build_bundle(where: Path) -> Path:
    bundle = where / "sdlc-init-bundled.py"
    made = subprocess.run(
        [sys.executable, str(INSTALLER), "--bundle", str(bundle)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    assert made.returncode == 0 and bundle.is_file(), f"{made.stdout}\n{made.stderr}"
    return bundle


CADENCE = [
    ("--snapshot-cadence", "snapshot-publish.yml", "SNAPSHOT_WORKFLOW_REL"),
    ("--corpus-replay", "corpus-replay.yml", "REPLAY_WORKFLOW_REL"),
]


@pytest.mark.parametrize("flag,template,rel_attr", CADENCE)
def test_the_bundle_installs_each_on_demand_cadence_template(
        tmp_path, flag, template, rel_attr):
    """RED before the fix: the bundle embeds 11 templates and neither list holds
    the cadence templates, so the bundle run with either flag exits 1 with
    `template '<name>' not found`. Spawned in a repo with an origin and a workspace
    file, because that is the adopter's real path."""
    bundle = _build_bundle(_harness_with_origin(tmp_path))
    target = _cadence_target(tmp_path)
    ran = subprocess.run(
        [sys.executable, str(bundle), "--target", str(target),
         "--workspace", str(target / "sdlc-workspace.toml"), flag, "--no-graph"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    out = ran.stdout + ran.stderr
    assert f"template '{template}' not found" not in out, \
        f"the bundle is missing {template}:\n{out}"
    assert ran.returncode == 0, out
    rel = getattr(SI, rel_attr)
    assert (target / rel).is_file(), f"{rel} was not written by the bundle:\n{out}"


def test_dump_templates_delivers_the_two_cadence_templates(tmp_path):
    """`--dump-templates` must deliver the SAME set the bundle embeds. RED before
    the fix: it wrote 11 and omitted both cadence templates. Named explicitly (not
    derived) so this half of the contract cannot agree with the code by
    construction."""
    dest = tmp_path / "dump"
    dest.mkdir()
    ran = subprocess.run(
        [sys.executable, str(INSTALLER), "--dump-templates", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (dest / "snapshot-publish.yml").is_file(), "--dump-templates omitted snapshot-publish.yml"
    assert (dest / "corpus-replay.yml").is_file(), "--dump-templates omitted corpus-replay.yml"


def test_dump_templates_delivers_the_whole_derived_set(tmp_path):
    """And every OTHER template the loader can be asked for, so a THIRD on-demand
    template added tomorrow is covered without editing this test."""
    dest = tmp_path / "dump"
    dest.mkdir()
    ran = subprocess.run(
        [sys.executable, str(INSTALLER), "--dump-templates", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    missing = [n for n in SI._loaded_template_names() if not (dest / n).is_file()]
    assert not missing, f"--dump-templates omitted derived templates: {missing}"


def test_the_bundled_set_is_derived_from_the_call_sites():
    """The durable half: the set is EXACTLY what load_template() is called with
    across the module, derived from the source. So the cadence templates are present
    because they are LOADED, not because somebody remembered to list them."""
    derived = set(SI._loaded_template_names())
    assert "snapshot-publish.yml" in derived and "corpus-replay.yml" in derived
    # a FROZEN roster of the base templates, written down here (not read from the
    # code) so this control cannot agree with the implementation by construction
    must_bundle = {"SKILL.md", "GRAPH-SKILL.md", "review-rubric.md", "plan-template.md",
                   "profile.yml", "ledger-check.yml", "policy.yml",
                   "snapshot-publish.yml", "corpus-replay.yml"}
    assert must_bundle <= derived, f"derived set dropped a base template: {sorted(must_bundle - derived)}"
    assert set(SI.EXTRA_TEMPLATE_FILES) <= derived, "the EXTRA loop was not resolved"
    for name in derived:
        assert (ROOT / "sdlc_templates" / name).is_file(), f"{name} is not a real template"


# ── clause 4: a test fails if a NEW template is loaded without being added ────

_PLANT_SRC = '''
TEMPLATE_FILES = ["a.md", "b.md"]
EXTRA_TEMPLATE_FILES = ["e/one.py"]
def load_template(name): ...
def somewhere():
    load_template("planted-positional.yml")
    load_template(name="planted-keyword.yml")
    return [load_template(n) for n in EXTRA_TEMPLATE_FILES]
def other():
    load_template("a.md")
def do_bundle(out):
    names = _loaded_template_names()
    return [load_template(n) for n in names]
'''


def test_a_newly_loaded_template_is_forced_into_the_derived_set():
    """PLANTED. A source that loads new templates yields them in the derived set --
    proving the set reads CALL SITES, not a frozen list. Both spellings are planted:
    positional AND the `name=` keyword, because the keyword form is exactly the
    evasion a reviewer used to reintroduce this row's defect past the first fix.
    `b.md` is listed but never loaded, so it is ABSENT: the list is not the source,
    the calls are. If the derivation were a static list this test fails."""
    got = set(SI._loaded_template_names(source=_PLANT_SRC))
    assert "planted-positional.yml" in got, "a positional literal was not picked up"
    assert "planted-keyword.yml" in got, \
        "a name= KEYWORD load was not picked up -- the derivation reads only positional args"
    assert "e/one.py" in got, "the list-constant loop feeding load_template was not resolved"
    assert "a.md" in got, "a literal load_template call was missed"
    assert "b.md" not in got, "a LISTED-but-unloaded name leaked in -- the set reads the list, not the calls"


@pytest.mark.parametrize("spelling", [
    'def f(x):\n    load_template(x + ".yml")\n',              # computed positional
    'def f(x):\n    load_template(name=x)\n',                  # computed keyword
    'def f(d):\n    load_template(**d)\n',                     # kwargs unpack
    'g = load_template\n',                                     # alias
    'import functools\nh = functools.partial(load_template, "z.yml")\n',  # partial
    't = [load_template][0]\n',                                # lookup in a container
])
def test_the_derivation_refuses_a_spelling_it_cannot_read(spelling):
    """Every spelling the AST cannot resolve to concrete names must be REFUSED, not
    silently dropped -- a computed argument, **kwargs, and any BARE reference to the
    loader (alias, partial, container lookup). This is defect-class-17 defence: a
    member of the set the enumeration cannot see must fail loudly rather than pass."""
    src = "def load_template(name): ...\n" + spelling
    with pytest.raises(SystemExit):
        SI._loaded_template_names(source=src)


def test_the_loud_refusal_is_a_process_not_a_function(tmp_path):
    """In process you cannot tell a refusal from a crash (pytest renders both as a
    failing test), so the refusal's contract is exercised as a PROCESS. A copy of the
    real installer carrying one unresolvable load_template call is spawned;
    --dump-templates runs the derivation first, so it must exit non-zero AND print the
    refusal a person reads."""
    src = INSTALLER.read_text(encoding="utf-8")
    injected = src.replace(
        "def main(argv",
        'def _t421_probe(suffix):\n    return load_template("dynamic-" + suffix)\n\n\ndef main(argv',
        1)
    assert injected != src, "injection anchor 'def main(argv' not found"
    copy = tmp_path / "sdlc-init-injected.py"
    copy.write_text(injected, encoding="utf-8")
    ran = subprocess.run(
        [sys.executable, str(copy), "--dump-templates", str(tmp_path / "out")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = ran.stdout + ran.stderr
    assert ran.returncode != 0, f"an unresolvable load_template call must refuse, got exit 0:\n{out}"
    assert "cannot resolve to a template name" in out, out


# ── a check of a DIFFERENT KIND: behaviour, not text (spelling-proof) ─────────
#
# Any AST derivation is blind to some spelling by construction. The completeness
# proof therefore does not read the source at all: it spawns the bundle with every
# flag the parser accepts and asserts none fails template-not-found. A on-demand load
# written in a spelling the derivation missed is caught HERE, at the adopter's door.


def _installer_flags() -> list[str]:
    """Every long flag the parser accepts, read off its own --help so the list is
    spelling-proof and cannot go stale as flags are added."""
    proc = subprocess.run([sys.executable, str(INSTALLER), "--help"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    return sorted(set(re.findall(r"--[a-z][a-z-]+", proc.stdout)))


def test_no_flag_makes_the_bundle_fail_template_not_found(tmp_path):
    """The spelling-proof completeness check. The bundle is spawned with EVERY flag,
    in a repo with an origin and a workspace, and none may fail template-not-found.
    A flag whose path loads a template the bundle does not embed fails here even if
    the derivation could not see the call -- which is what the reviewer's keyword
    plant demonstrated the text-level check alone cannot guarantee."""
    bundle = _build_bundle(_harness_with_origin(tmp_path))
    flags = _installer_flags()
    assert {"--snapshot-cadence", "--corpus-replay"} <= set(flags), flags
    takes_value = {"--tools": "all", "--bundle": str(tmp_path / "reb.py"),
                   "--dump-templates": str(tmp_path / "dt")}
    failures = []
    for flag in flags:
        if flag in ("--help", "--target", "--workspace"):
            continue  # --target/--workspace are supplied by the base command below
        target = _cadence_target(tmp_path / ("t" + flag.strip("-")))
        cmd = [sys.executable, str(bundle), "--target", str(target),
               "--workspace", str(target / "sdlc-workspace.toml"), "--no-graph", flag]
        if flag in takes_value:
            cmd.append(takes_value[flag])
        ran = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=600)
        if "not found (no sdlc_templates" in (ran.stdout + ran.stderr):
            failures.append(f"{flag}:\n{ran.stdout}\n{ran.stderr}")
    assert not failures, "a flag made the bundle load a template it does not embed:\n" + "\n".join(failures)


def test_the_bundle_installs_every_activated_template_in_one_run(tmp_path):
    """The positive completeness proof: one bundle run activating every load_template
    path -- base install, --claude-extras, and BOTH cadence jobs -- exits 0 and writes
    both workflows, so all embedded templates resolve at the door at once."""
    bundle = _build_bundle(_harness_with_origin(tmp_path))
    target = _cadence_target(tmp_path)
    ran = subprocess.run(
        [sys.executable, str(bundle), "--target", str(target),
         "--workspace", str(target / "sdlc-workspace.toml"), "--tools", "all",
         "--claude-extras", "--snapshot-cadence", "--corpus-replay", "--no-graph"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = ran.stdout + ran.stderr
    assert "not found (no sdlc_templates" not in out, out
    assert ran.returncode == 0, out
    assert (target / SI.SNAPSHOT_WORKFLOW_REL).is_file(), out
    assert (target / SI.REPLAY_WORKFLOW_REL).is_file(), out
