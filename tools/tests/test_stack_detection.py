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
import subprocess
import sys
from pathlib import Path

import pytest

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
    return load_installer()


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
    r1 = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path),
         "--single-component", "--tools", "claude"],
        capture_output=True, text=True)
    assert r1.returncode == 0, f"first install failed: {r1.stdout}{r1.stderr}"

    write(tmp_path, "services/c/pom.xml", "<project/>\n")  # tree grows: 2 -> 3

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
