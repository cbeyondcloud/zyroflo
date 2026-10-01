"""Stack detection: what a repository IS, when it is honestly more than one thing.

The defect these pin was found by pointing the harness at a real workspace (item 76) and
is worth restating, because every test below is a shape of it: `detect_stack` was a
**first-match chain**, so the stack a repository got depended on the order the branches
happened to be written in rather than on the evidence. Python sat first and matched on a
root `requirements.txt`, a `tests/` directory, or any `test_*.py` anywhere in the tree —
so a component holding 3,288 JavaScript, 455 TypeScript and 301 Java files, with four
`pom.xml` and ten `test_*.py`, was handed `pytest -q`.

**That is strictly worse than the `unknown` the item was opened about.** A blank command
is a visible hole a developer fills. A confident wrong command runs, fails for reasons
that have nothing to do with their code, and teaches them the gate is noise — this
project's "a wrong answer at exit 0 beats an error" failure, in a fifth module.

The fix grades evidence in tiers (root manifest / nested manifest / weak signal) and
**reports every stack it finds** instead of picking one by position, leaving the commands
blank with a stated reason whenever running them from the root would be wrong. Detection
may report a collision; it may not pick the winner — the same rule `mark-canonical`
follows one layer down.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import _project  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def si():
    return _project.filled_gate(load_installer())


def write(root: Path, rel: str, body: str = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── the reported defect ────────────────────────────────────────────────────────
def test_a_stray_test_file_does_not_claim_a_java_repository(si, tmp_path: Path):
    """The exact shape item 76 found, reduced: nested Maven modules and a handful of
    Python test files. Before the fix this returned `python` and `pytest -q`."""
    for module in ("orders", "billing", "shipping", "notify"):
        write(tmp_path, f"services/{module}/pom.xml", "<project/>")
    for i in range(10):
        write(tmp_path, f"scripts/test_helper{i}.py", "def test_x(): pass\n")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "java-maven", "a build manifest must outrank a stray test file"
    assert "python" not in d["stacks"] or d["stacks"].index("java-maven") == 0
    assert d["test_command"] == "", "a nested manifest cannot be run from the root"
    assert "below the repository root" in d["stack_note"].lower()


def test_a_root_requirements_txt_does_not_make_a_java_repository_python(si, tmp_path: Path):
    """`requirements.txt` at the root of a non-Python repository is routine — mkdocs,
    pre-commit, a CI helper. It used to claim the repository outright, at root level,
    with no recursion involved at all."""
    write(tmp_path, "pom.xml", "<project/>")
    write(tmp_path, "requirements.txt", "mkdocs==1.6.0\n")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "java-maven"
    assert d["test_command"] == "mvn -q test", "unrivalled root manifest: commands stand"


def test_a_bare_tests_directory_is_no_longer_evidence_of_anything(si, tmp_path: Path):
    """`tests/` is a universal convention, so it said nothing while claiming everything.
    A Go repository with one is a Go repository."""
    write(tmp_path, "go.mod", "module x\n")
    (tmp_path / "tests").mkdir()
    write(tmp_path, "tests/main_test.go", "package tests\n")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "go" and d["test_command"] == "go test ./..."
    assert "python" not in d["stacks"]


def test_a_javascript_monorepo_is_not_unknown(si, tmp_path: Path):
    """The JavaScript branch was root-only, so the layout that language is most likely to
    use — packages with no root manifest — fell through every branch to `unknown`."""
    for pkg in ("web", "admin", "shared"):
        write(tmp_path, f"packages/{pkg}/package.json", "{}")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "javascript", "nested package.json must still name the stack"
    assert d["stacks"] == ["javascript"]


# ── reporting rather than guessing ─────────────────────────────────────────────
def test_rival_claims_at_the_root_leave_the_commands_blank_and_say_why(si, tmp_path: Path):
    """Two build manifests at the root is the case nothing in the repository can settle.
    Before the fix chain order silently answered it — `package.json` was consulted before
    `pom.xml`, so this returned `javascript` and a confident `npm test`."""
    write(tmp_path, "pom.xml", "<project/>")
    write(tmp_path, "package.json", "{}")

    d = si.detect_stack(tmp_path)

    assert set(d["stacks"]) == {"java-maven", "javascript"}
    assert d["test_command"] == "" and d["coverage_command"] == "" and d["lint_command"] == ""
    assert "declare themselves equally" in d["stack_note"]
    assert "java-maven" in d["stack_note"] and "javascript" in d["stack_note"], \
        "a note that does not name the rivals cannot be acted on"


def test_every_stack_found_is_reported_not_just_the_winner(si, tmp_path: Path):
    """The item's decision, stated as a test: a repository with three stacks has three,
    and the profile records all of them."""
    write(tmp_path, "services/api/pom.xml", "<project/>")
    write(tmp_path, "web/package.json", "{}")
    write(tmp_path, "tools/pyproject.toml", "[project]\nname='t'\n")

    d = si.detect_stack(tmp_path)

    assert set(d["stacks"]) == {"java-maven", "javascript", "python"}
    assert d["stack"] in d["stacks"]


# ── no regression for the ordinary single-stack repository ─────────────────────
def test_a_plain_python_repository_is_unchanged(si, tmp_path: Path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "tests/test_app.py", "def test_x(): assert True\n")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "python"
    assert d["test_command"] == "pytest -q"
    assert d["coverage_command"] == "pytest --cov --cov-report=term-missing"
    assert d["stack_note"] == "", "an unambiguous repository gets no warning"


def test_a_tooling_package_json_does_not_disturb_a_python_repository(si, tmp_path: Path):
    """A `package.json` for prettier is a tooling file, not a rival claim — and blanking
    the commands for it would trade one wrong answer for a useless one. Both are at the
    root, so this is exactly where the tier rule has to NOT overreach: Python wins on
    marker count, and the note stays empty."""
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "setup.cfg", "[metadata]\n")
    write(tmp_path, "package.json", '{"devDependencies": {"prettier": "3.0.0"}}')

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "python" and d["test_command"] == "pytest -q"


def test_an_empty_repository_is_unknown_with_no_stacks(si, tmp_path: Path):
    write(tmp_path, "README.md", "# hi\n")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "unknown" and d["stacks"] == []
    assert d["test_command"] == "" and d["stack_note"] == ""


# ── the traversal itself ───────────────────────────────────────────────────────
def test_the_scan_does_not_descend_into_excluded_directories(si, tmp_path: Path):
    """Before the fix, `**/test_*.py` walked the WHOLE tree — `node_modules` included —
    on every repository the cheap root checks missed, invisibly, on every detection."""
    write(tmp_path, "go.mod", "module x\n")
    write(tmp_path, "node_modules/left-pad/package.json", "{}")
    write(tmp_path, "target/generated/pom.xml", "<project/>")
    write(tmp_path, ".venv/lib/pyproject.toml", "[project]\nname='v'\n")

    d = si.detect_stack(tmp_path)

    assert d["stacks"] == ["go"], (
        "a dependency tree is not evidence about the repository that vendors it; "
        f"got {d['stacks']}")


def test_the_scan_depth_is_bounded_and_the_bound_is_stated(si, tmp_path: Path):
    """A bound nobody can see is a bound nobody maintains. This pins the documented
    limit so deepening it is a decision with a test behind it, not a drift.

    **The number is measured, not guessed.** Swept against a real 26-component workspace:
    depth 2 left 11 components with no manifest found, depth 3 left 9, and depths 5 and 8
    also left 9 — the same nine, which carry no build manifest at any depth. So 3 is the
    knee of the curve rather than a round number, and it costs ~0.1s where depth 8 costs
    ~0.9s for identical output. Raising it buys nothing; lowering it loses real components.
    """
    assert si._SCAN_MAX_DEPTH == 3
    write(tmp_path, "a/b/c/d/e/pom.xml", "<project/>")

    d = si.detect_stack(tmp_path)

    assert d["stack"] == "unknown", "evidence past the bound is not found, by design"


def test_an_unreadable_directory_does_not_abort_the_scan(si, tmp_path: Path, monkeypatch):
    """Detection runs against other people's disks. One unreadable directory must cost
    its own contents and nothing else — the same rule the build follows for a malformed
    config file."""
    write(tmp_path, "pom.xml", "<project/>")
    (tmp_path / "locked").mkdir()
    real_iterdir = Path.iterdir

    def explode(self):
        if self.name == "locked":
            raise PermissionError("denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", explode)
    assert si.detect_stack(tmp_path)["stack"] == "java-maven"


# ── the contract with the rest of the harness ──────────────────────────────────
def test_every_stack_with_a_marker_has_a_command_builder(si):
    """The failure this prevents: add a stack to the marker table, forget the command
    table, and every repository of that stack detects correctly and then hands the
    developer a blank command with no note explaining it. Same shape as
    `workspace_report.STACK_PROBES`, and pinned the same way."""
    declared = {stack for stack, _, _ in si._STRONG_MARKERS}
    assert declared == set(si._COMMANDS), (
        f"marker table and command table disagree: "
        f"{declared ^ set(si._COMMANDS)}")


def test_the_scan_exclusions_still_mirror_the_graph_layers(si):
    """`sdlc-init.py` is stdlib-only by gate, so it cannot import `repo_walk` and the
    exclusion list is duplicated. Bounded duplication is the price of the zero-dependency
    install path; this is what keeps the copies from drifting apart in silence."""
    from tools.graph.core.lib import repo_walk

    assert set(repo_walk.DEFAULT_EXCLUDES) == set(si._SCAN_EXCLUDES), (
        "the installer's scan exclusions and repo_walk's have diverged: "
        f"{set(repo_walk.DEFAULT_EXCLUDES) ^ set(si._SCAN_EXCLUDES)}")


def test_the_profile_records_stacks_and_the_note(si, tmp_path: Path):
    """The value of reporting several stacks is zero if it stops at the return value —
    it has to survive into the file a human opens."""
    write(tmp_path, "pom.xml", "<project/>")
    write(tmp_path, "package.json", "{}")
    profile = si.detect_stack(tmp_path)
    profile["vcs"] = {}

    rendered = si.render_profile(profile, ["claude"])

    assert "@@" not in rendered, "unrendered placeholder"
    import yaml
    d = yaml.safe_load(rendered)
    assert set(d["stacks"]) == {"java-maven", "javascript"}
    assert "declare themselves equally" in d["stack_note"]
    assert d["test_command"] == ""


# ── a monorepo is not one component, and the installer is where that is asked ──
#
# The field run measured what the silent default costs, twice, in one afternoon:
# P2 scoping mapped a whole repository as a single node carrying 1,189 edges, and
# P5's supply-chain gate inherited every dependency in the tree and reported 102
# blocking findings against a two-file change. Neither was fixable once the run
# had started, because the gates pin artifacts that name the model. So it is
# refused at the one moment somebody is looking.


def test_a_manifest_at_the_root_is_one_build_however_many_are_below_it(si, tmp_path):
    """The control, and the discrimination that matters: a repository declaring
    how it is built AT ITS ROOT builds as a whole. This harness is that shape -
    95 python markers, `multi` False - and must never be refused."""
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "packages/a/pyproject.toml", "[project]\nname='a'\n")
    write(tmp_path, "packages/b/pyproject.toml", "[project]\nname='b'\n")
    assert si.build_units(tmp_path)["multi"] is False


def test_several_nested_manifests_of_one_stack_are_several_build_units(si, tmp_path):
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    units = si.build_units(tmp_path)
    assert units["multi"] is True and units["stack"] == "java-maven"
    assert units["dirs"] == ["services/a", "services/b"], \
        "a refusal that cannot name a directory is one nobody can act on"


def test_build_units_deeper_than_the_stack_scan_reads_are_counted(si, tmp_path):
    """T569 (batch-6 review R2-2). The unit count stopped at the stack scan's depth
    bound - three levels, a heuristic for detecting a stack that T515 made
    load-bearing - so a repository whose units sit deeper was refused, recorded
    (`units: N`) and reasoned about on a count that is not its unit count. The stack is
    still detected within the stated bound; its units are counted by a walk that bound
    does not stop, pruned as the scan is."""
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    write(tmp_path, "platform/core/modules/c/pom.xml", "<project/>\n")
    write(tmp_path, "platform/core/modules/sub/d/pom.xml", "<project/>\n")
    write(tmp_path, "node_modules/x/y/z/pom.xml", "<project/>\n")
    units = si.build_units(tmp_path)
    assert units["multi"] is True and units["count"] == 4, units
    assert units["dirs"] == ["platform/core/modules/c", "platform/core/modules/sub/d",
                             "services/a", "services/b"], units


def test_a_directory_link_back_up_the_tree_is_not_walked(si, tmp_path):
    """Review B12-3. T569's unbounded count followed directory links, so an ordinary
    `docs/latest -> ..` link counted each unit once per level - 82 units on Linux and
    128 on Windows, for 2. A link is not entered, as `os.walk` and `repo_walk` do not
    enter one: a symlink on POSIX, a junction on Windows (made without privilege)."""
    write(tmp_path, "services/x/pom.xml", "<project/>\n")
    write(tmp_path, "services/y/pom.xml", "<project/>\n")
    (tmp_path / "docs").mkdir()
    link = tmp_path / "docs" / "latest"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(tmp_path), str(link))
    else:
        os.symlink("..", link, target_is_directory=True)
    try:
        units = si.build_units(tmp_path)
    finally:
        # Removed as a link, never walked into.
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
    assert units["count"] == 2 and units["dirs"] == ["services/x", "services/y"], units


def test_one_nested_manifest_is_not_several(si, tmp_path):
    """A backend under a root that declares nothing is still one build."""
    write(tmp_path, "backend/pom.xml", "<project/>\n")
    assert si.build_units(tmp_path)["multi"] is False


def test_an_empty_repository_is_not_a_monorepo(si, tmp_path):
    assert si.build_units(tmp_path)["multi"] is False


def test_installing_a_multi_unit_repository_as_one_is_refused(si, tmp_path, capsys):
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    code = si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False)
    out = capsys.readouterr().out
    assert code == 1, "1 is this installer's 'nothing was installed'"
    assert not (tmp_path / ".sdlc").exists(), \
        "a half-install whose model is wrong is worse than no install"
    assert "services/a" in out and "services/b" in out
    assert "--workspace" in out and "--single-component" in out, \
        "a refusal must name both ways forward or it is a dead end"


def test_the_single_component_assertion_is_recorded_where_a_reader_will_find_it(
        si, tmp_path):
    """The override is not a silence. A run that turns out repository-wide has
    to be traceable to somebody having said so."""
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    code = si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False,
                      single_component=True)
    assert code == 0
    note = (tmp_path / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert "--single-component" in note and "2 java-maven build units" in note
    assert "you build from. 2 java-maven" in note, \
        "the two sentences are joined, not run together"


def test_a_rerun_without_the_flag_honours_the_recorded_single_component_assertion(
        tmp_path):
    """T244 round 2: the profile records a STRUCTURED `single_component:` key
    (never a substring of free text - review W376 BLOCKED round 1 because its
    whole-document grep read an adopter's comment saying they did NOT use the
    flag as though it were an assertion). A re-run on the same target without
    the flag must read that record and skip the refusal rather than asking the
    adopter to repeat themselves - and say so out loud, naming the file the
    decision came from, so the override is never a silence (review finding 2).

    Tested by spawning the installer twice (a CLI's contract is a process).
    """
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")

    # Run 1: install with --single-component
    _project.seed(tmp_path)
    r1 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--single-component", "--tools", "claude"],
        capture_output=True, text=True)
    assert r1.returncode == 0, f"first install failed: {r1.stdout}{r1.stderr}"
    profile = (tmp_path / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert "--single-component" in profile, "the assertion was not recorded"
    assert "single_component:" in profile and "asserted: true" in profile \
        and "units: 2" in profile, \
        "the assertion must be a STRUCTURED key, not only prose in stack_note"

    # Run 2: re-run WITHOUT --single-component; the existing profile's
    # recorded assertion should be honoured.
    _project.seed(tmp_path)
    r2 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--tools", "claude"],
        capture_output=True, text=True)
    assert r2.returncode == 0, (
        f"re-run refused despite the profile carrying the recorded assertion: "
        f"{r2.stdout}")
    assert "refusing" not in r2.stdout, (
        "a re-run printed a refusal even though the profile records "
        "--single-component")
    # Review finding 2: "silence" - the honoured re-run must say what it did and
    # where the decision came from, not merely fail to refuse.
    assert ("honouring the --single-component assertion recorded in "
            ".sdlc/profile.yml") in r2.stdout, (
        f"the honoured re-run must name the source of the decision on stdout: "
        f"{r2.stdout}")
    # The profile written by run 2 must be byte-identical to run 1's (same tree,
    # same assertion) - `preserve` reports it `= unchanged`, never a `.new`.
    assert not (tmp_path / ".sdlc" / "profile.yml.new").exists(), (
        "an honoured re-run must not fork the profile")


def test_a_profile_that_only_mentions_the_flag_in_prose_still_refuses(tmp_path):
    """T244 round 2 (review W376, finding 1 / row check 1): a comment merely
    MENTIONING `--single-component`, without a structured assertion, must not
    be read as one. This is the exact adversarial profile the review built -
    an adopter's note saying they explicitly did NOT use the flag - which the
    round-1 guard's whole-document substring grep silently honoured anyway."""
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    write(tmp_path, ".sdlc/profile.yml",
          "# Team decision 2026-09: we did NOT use --single-component here;\n"
          "# declare components with --workspace instead.\n"
          "schema_version: 2\nscope: repo\n")

    _project.seed(tmp_path)
    r = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--tools", "claude"],
        capture_output=True, text=True)
    assert r.returncode == 1, (
        f"a prose mention of the flag must not stand in for a recorded "
        f"assertion: {r.stdout}")
    assert "refusing" in r.stdout


def test_a_structured_record_that_is_not_asserted_still_refuses(tmp_path):
    """The `single_component:` key present with `asserted: false` (the shape a
    fresh single-unit install, or a hand-edit, could leave behind) is not a
    yes. Guards the boolean half of the honour condition the same way the
    prose test above guards the format half."""
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    write(tmp_path, ".sdlc/profile.yml",
          "schema_version: 2\nscope: repo\n"
          "single_component:\n  asserted: false\n  units: 2\n")

    _project.seed(tmp_path)
    r = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--tools", "claude"],
        capture_output=True, text=True)
    assert r.returncode == 1, (
        f"asserted: false must not be read as a recorded yes: {r.stdout}")
    assert "refusing" in r.stdout


def test_a_grown_tree_is_asked_again_rather_than_governed_by_an_old_yes(tmp_path):
    """T244 round 2 (review W376, row check 3 / the sticky-is-unbounded note): a
    yes recorded over 2 units must not silently govern a tree that has since
    grown to 3 - the refusal fires again and quotes BOTH counts, so P2 scoping
    and P5's supply-chain gate are not silently widened past what anyone
    actually asserted."""
    write(tmp_path, "services/a/pom.xml", "<project/>\n")
    write(tmp_path, "services/b/pom.xml", "<project/>\n")
    _project.seed(tmp_path)
    r1 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--single-component", "--tools", "claude"],
        capture_output=True, text=True)
    assert r1.returncode == 0, f"first install failed: {r1.stdout}{r1.stderr}"

    write(tmp_path, "services/c/pom.xml", "<project/>\n")  # tree grows: 2 -> 3

    _project.seed(tmp_path)
    r2 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--tools", "claude"],
        capture_output=True, text=True)
    assert r2.returncode == 1, (
        f"a grown tree must be asked again, not silently governed by an old "
        f"yes: {r2.stdout}")
    assert "refusing" in r2.stdout
    assert ("it was made over 2 units and this tree now has 3" in r2.stdout), (
        f"the refusal must quote BOTH the recorded and the current count: "
        f"{r2.stdout}")

    # sdlc-init.py's own rule (no_workspace_in_checkout_reason's docstring):
    # "the remedy has to survive being FOLLOWED." Plain --single-component
    # exits 0 but leaves the stale record on disk (profile.yml is PRESERVED,
    # not force-written), so the refusal must name --force - and following
    # that literally must actually clear the mismatch.
    assert "--single-component --force" in r2.stdout, (
        f"the refusal must name the command that actually updates the stale "
        f"record, not one that silently changes nothing: {r2.stdout}")

    _project.seed(tmp_path)
    r3 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--single-component", "--force", "--tools", "claude"],
        capture_output=True, text=True)
    assert r3.returncode == 0, (
        f"following the printed remedy literally must succeed: "
        f"{r3.stdout}{r3.stderr}")

    _project.seed(tmp_path)
    r4 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--tools", "claude"],
        capture_output=True, text=True)
    assert r4.returncode == 0, (
        f"after following the remedy, a plain re-run must proceed - the "
        f"record was supposed to have been updated to 3 units: {r4.stdout}")
    assert "refusing" not in r4.stdout


# ── T515: a build unit is a directory with a build manifest, and nothing else ──
#
# The count mixed the weak markers - a root `requirements.txt`, any `conftest.py`,
# any `test_*.py` - in with the manifests. So the refusal printed a wrong number,
# the record carried it, and one test file added at the root made the recorded
# assertion stale and the next plain run refuse.


def _two_units_and_three_passengers(root: Path) -> None:
    """Two build units, and five markers: the review's own reproduction."""
    write(root, "requirements.txt", "requests\n")
    write(root, "test_root.py", "def test_x():\n    pass\n")
    write(root, "services/a/pyproject.toml", "[project]\nname = 'a'\n")
    write(root, "services/a/setup.py", "")
    write(root, "services/b/pyproject.toml", "[project]\nname = 'b'\n")


def _spawn(root: Path, *extra: str) -> subprocess.CompletedProcess:
    _project.seed(root)
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(root), "--tools", "claude",
         "--no-graph", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)


def test_the_unit_count_is_the_manifest_directories_not_every_marker(si, tmp_path):
    """T515: "5 below it, in services/a, services/b" named two directories and
    counted five things - two root files and both manifests of one unit."""
    _two_units_and_three_passengers(tmp_path)
    units = si.build_units(tmp_path)
    assert units["multi"] is True and units["count"] == 2, units


def test_the_refusal_prints_the_unit_count(tmp_path):
    _two_units_and_three_passengers(tmp_path)
    run = _spawn(tmp_path)
    assert run.returncode == 1, run.stdout
    assert "and 2 below it, in services/a, services/b." in run.stdout, run.stdout


def test_a_test_file_added_later_does_not_void_a_recorded_assertion(tmp_path):
    """T515: an unchanged build structure leaves the assertion standing. The added
    file was the whole cause of the refusal whose remedy (`--force`) replaces
    every preserved file."""
    _two_units_and_three_passengers(tmp_path)
    assert _spawn(tmp_path, "--single-component").returncode == 0
    profile = (tmp_path / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert "units: 2" in profile, "the record carries the unit count"

    write(tmp_path, "test_another.py", "def test_y():\n    pass\n")
    run = _spawn(tmp_path)

    assert run.returncode == 0 and "refusing" not in run.stdout, run.stdout
    assert "honouring the --single-component assertion" in run.stdout, run.stdout


def _record(root: Path, units: int) -> None:
    """Rewrite the recorded assertion's unit count, as an older installer or a hand
    edit would have left it."""
    profile = root / ".sdlc" / "profile.yml"
    text, n = re.subn(r"(?m)^(\s+units:\s*)\d+", rf"\g<1>{units}",
                      profile.read_text(encoding="utf-8"))
    assert n == 1, "the fixture must find the one record it rewrites"
    profile.write_text(text, encoding="utf-8")


def test_a_record_an_older_installer_wrote_is_asked_about_once(tmp_path):
    """Migration. Before T515 the record carried every marker the scan found, so a
    profile written then says `units: 5` about this two-unit tree. That number
    cannot say how many UNITS the yes was about, and batch-6 review B6-2 grew a tree
    by a unit that the older count still matched. So it is asked about once, in the
    words that explain the two numbers, and the edit it names settles it for good,
    without `--force`."""
    _two_units_and_three_passengers(tmp_path)
    assert _spawn(tmp_path, "--single-component").returncode == 0
    _record(tmp_path, 5)

    asked = _spawn(tmp_path)

    assert asked.returncode == 1 and "refusing" in asked.stdout, asked.stdout
    assert "or an older installer wrote it" in asked.stdout, asked.stdout
    assert "counted that way this tree has exactly 5" in asked.stdout, asked.stdout
    assert "set `units: 2`" in asked.stdout, asked.stdout

    _record(tmp_path, 2)                                # the named edit
    settled = _spawn(tmp_path)
    assert settled.returncode == 0 and "refusing" not in settled.stdout, settled.stdout
    assert "honouring the --single-component assertion" in settled.stdout


def test_an_older_count_that_still_matches_cannot_cover_a_new_unit(tmp_path):
    """Review B6-2's reproduction: a unit is added and a test file removed, so the
    older marker count is unchanged at 5 while the units went from 2 to 3. Honouring
    the older record then would be a yes about 2 read as a yes about 3."""
    _two_units_and_three_passengers(tmp_path)
    assert _spawn(tmp_path, "--single-component").returncode == 0
    _record(tmp_path, 5)
    write(tmp_path, "services/c/pyproject.toml", "[project]\nname = 'c'\n")
    (tmp_path / "test_root.py").unlink()

    run = _spawn(tmp_path)

    assert run.returncode == 1 and "refusing" in run.stdout, run.stdout
    assert "set `units: 3`" in run.stdout, run.stdout


#: Every cause a record reading higher than the tree can have, as the refusal names
#: them: the numbers alone cannot tell them apart.
EVERY_CAUSE = ("the numbers cannot say why. Units were lost or moved into a directory "
               "the count skips (a dot-directory, or a vendored or build tree), the "
               "number was edited by hand, or an older installer wrote it")


def _asserted_over_five(si, root: Path, capsys) -> None:
    """This installer's own record, over five real manifest directories."""
    for name in "abcde":
        write(root, f"services/{name}/pyproject.toml", f"[project]\nname = '{name}'\n")
    assert si.install(root, ["claude"], force=False, dry_run=False, graph=False,
                      single_component=True) == 0
    capsys.readouterr()


def _refusal(si, root: Path, capsys) -> str:
    assert si.install(root, ["claude"], force=False, dry_run=False, graph=False) == 1
    return capsys.readouterr().out


def test_a_shrunk_tree_is_not_blamed_on_an_older_installer(si, tmp_path, capsys):
    """Review B6-4: a record this installer wrote over five units, on a tree that now
    has two, was refused with "an installer before this one counted test files" -
    a cause nothing here established. The refusal names every cause it cannot rule
    out."""
    _asserted_over_five(si, tmp_path, capsys)
    for name in "cde":
        (tmp_path / "services" / name / "pyproject.toml").unlink()

    out = _refusal(si, tmp_path, capsys)
    assert "made over 5 units and this tree now has 2" in out, out
    assert EVERY_CAUSE in out, out
    assert "counted that way" not in out, "no older count matches, so none is claimed"


def test_an_older_count_that_matches_by_chance_still_names_every_cause(
        si, tmp_path, capsys):
    """Delta review R1-2: when the record also equals the older way of counting,
    only that reading was offered - though a record reading higher than the tree
    may equally be a tree that lost units. Here it did: three units went and three
    test files came, and the older count is 5 again."""
    _asserted_over_five(si, tmp_path, capsys)
    for name in "cde":
        (tmp_path / "services" / name / "pyproject.toml").unlink()
    for name in ("requirements.txt", "test_x.py", "conftest.py"):
        write(tmp_path, name, "")

    out = _refusal(si, tmp_path, capsys)
    assert EVERY_CAUSE in out, out
    assert "counted that way this tree has exactly 5" in out, out


def test_units_moved_deeper_are_still_counted_so_the_record_holds(si, tmp_path, capsys):
    """T569. Delta review R2-2 found units that merely moved deeper than the scan's
    three levels dropping out of the count while still there, so a record made over
    them was refused, and the move had to be named among the causes. The units are now
    counted by a walk the stack scan's bound does not stop, so moved units are still
    units, and the record made over them still holds."""
    _asserted_over_five(si, tmp_path, capsys)
    for name in "cde":
        deeper = tmp_path / "platform" / "apps" / "group" / name / "pyproject.toml"
        deeper.parent.mkdir(parents=True)
        (tmp_path / "services" / name / "pyproject.toml").rename(deeper)

    assert si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False) == 0
    out = capsys.readouterr().out
    assert "asserted over 5" in out and "still 5 today" in out, out


def test_a_hand_edited_record_is_one_of_the_causes_named(si, tmp_path, capsys):
    """Delta review R1-3: the refusal's own remedy is a hand edit, so a mistyped one
    is a cause it must not rule out. Nothing about this tree changed."""
    _asserted_over_five(si, tmp_path, capsys)
    _record(tmp_path, 9)

    out = _refusal(si, tmp_path, capsys)
    assert "made over 9 units and this tree now has 5" in out, out
    assert "the number was edited by hand" in out, out


def test_a_grown_tree_is_still_asked_again_under_the_new_count(tmp_path):
    """The control on the migration: an older record is honoured only while the
    tree is the one it was made over, so a unit added since is still asked about -
    and the refusal names the edit that reasserts it without `--force`, and what
    `--force` would cost."""
    _two_units_and_three_passengers(tmp_path)
    assert _spawn(tmp_path, "--single-component").returncode == 0
    write(tmp_path, "services/c/pyproject.toml", "[project]\nname = 'c'\n")

    run = _spawn(tmp_path)

    assert run.returncode == 1 and "refusing" in run.stdout, run.stdout
    assert "it was made over 2 units and this tree now has 3" in run.stdout, run.stdout
    assert "units: 3" in run.stdout, "the reassertion that preserves every file"
    assert "replaces every preserved file" in run.stdout, run.stdout


def test_a_refusal_counts_past_the_directories_it_lists(si, tmp_path, capsys):
    """The sample stops at six; the count must not. Collecting the directories was
    capped at the sample, so "and N more" could never print."""
    for i in range(8):
        write(tmp_path, f"services/s{i}/pom.xml", "<project/>\n")
    assert si.build_units(tmp_path)["count"] == 8
    code = si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False)
    out = capsys.readouterr().out
    assert code == 1, out
    assert "and 8 below it" in out and ", and 2 more" in out, out
