"""The defect flow — item 45.

`KINDS` has carried three run kinds since item 31 and **the pipeline treated
all three identically**: nothing read a run's kind, and no SOP carried an
obligation keyed to it. UC-06 promises *"a defect from symptom to fix plus
regression tests via the same pipeline"*, and "the same pipeline" had been
implemented as "no difference at all".

Two properties are worth more than the rest of this file, and both are about
what the check REFUSES to do:

**It does not grade prose.** A document can assert anything. The check requires
the reproduction to NAME its regression tests and then requires each name to be
in the change's own diff — the claim is checked against the work, not against a
rubric.

**It does not name an ecosystem.** A judge that looked for `test_`,
`*Test.java` or `*.spec.ts` would be a gate that knows about languages, and
item 40 settled that: nothing in the gate names an ecosystem. The run says what
its test is called; the harness checks that name is real.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import defect as D  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')

#: A diff that adds a test. Its exact shape does not matter to the check - what
#: matters is that a name either is or is not in it.
PATCH = """From abc Mon Sep 17 00:00:00 2001
Subject: [PATCH] fix the thing

diff --git a/app/thing.py b/app/thing.py
--- a/app/thing.py
+++ b/app/thing.py
@@ -1,2 +1,4 @@
-def thing():
-    return None
+def thing():
+    if not ready():
+        raise ValueError("not ready")
+    return 1
diff --git a/tests/test_thing.py b/tests/test_thing.py
--- /dev/null
+++ b/tests/test_thing.py
@@ -0,0 +1,4 @@
+def test_thing_refuses_when_not_ready():
+    with pytest.raises(ValueError):
+        thing()
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def open_run(repo: Path, kind: str = "defect") -> str:
    return L.create(repo, kind, "a title")["run_id"]


def impl(repo: Path, run_id: str, patch: str | None = PATCH) -> Path:
    folder = L.run_dir(repo, run_id) / ST.IMPL_DIR.rstrip("/")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("what changed\n", encoding="utf-8")
    if patch is not None:
        (folder / ST.CHANGE_PATCH).write_text(patch, encoding="utf-8")
    return folder


def repro(repo: Path, run_id: str, text: str, record: bool = True) -> Path:
    path = L.run_dir(repo, run_id) / ST.REPRO
    path.write_text(text, encoding="utf-8")
    if record:
        L.record_artifact(repo, run_id, ST.REPRO)
    return path



def repro_text(repo: Path, run_id: str) -> str:
    return (L.run_dir(repo, run_id) / ST.REPRO).read_text(encoding="utf-8")

GOOD = ("# Reproduction\n\nThe call returned None where it should refuse.\n\n"
        "Regression test: `test_thing_refuses_when_not_ready`.\n\n"
        "Against the unfixed source:\n\n    AssertionError: DID NOT RAISE\n")


# ── the kind is read, which is the whole item ───────────────────────────────


def test_a_defect_owes_a_reproduction(repo):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    owed = D.obligations(repo, run_id)
    assert owed and ST.REPRO in owed[0]


def test_a_requirement_owes_nothing_here(repo):
    """The point of reading the field at all. A gate that fired on every run
    would be a gate about nothing."""
    run_id = open_run(repo, "requirement")
    impl(repo, run_id)
    assert D.obligations(repo, run_id) == []


def test_a_deploy_change_owes_nothing_here(repo):
    run_id = open_run(repo, "deploy-change")
    impl(repo, run_id)
    assert D.obligations(repo, run_id) == []


def test_a_complete_reproduction_owes_nothing(repo):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id, GOOD)
    assert D.obligations(repo, run_id) == []


# ── the claim is checked against the change, not graded as prose ────────────


def test_a_test_named_only_in_prose_is_refused(repo):
    """The property this check exists for. A reproduction that names a test the
    change does not contain is a claim about work nobody did - and it is the
    exact shape a plausible-sounding agent produces."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id, "Regression test: `test_something_never_written`.\n")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1
    assert "test_something_never_written" in owed[0]
    assert "nobody did" in owed[0]


def test_a_reproduction_that_names_no_test_is_refused(repo):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id, "It was broken. I fixed it. Trust me.\n")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "names no regression test" in owed[0]


def test_a_stray_fence_marker_that_swallows_a_declaration_is_LOUD(repo):
    """**Item 149's reversal rests on this, so it is pinned and not assumed.**
    An unclosed fence now runs to the end of the document, so one stray
    backtick can swallow the line that names the regression test. That is only
    the safe direction while the swallow is LOUD: the reviewer measured the old
    rule returning `only_an_example.py` and losing the real name - a false
    obligation and a missed one, both plausible. Losing the name must instead
    land on the refusal this check already had."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    swallowed = ("# Reproduction" + chr(10) * 2
                 + "The P4 SOP asks a repro to write:" + chr(10) * 2
                 + "```markdown" + chr(10) * 2
                 + "Regression test: `test_thing_refuses_when_not_ready`."
                 + chr(10))
    assert swallowed.count("```") == 1, "one stray marker, never closed"
    repro(repo, run_id, swallowed)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "names no regression test" in owed[0], owed


def test_the_check_names_no_ecosystem(repo):
    """A judge that recognised tests by `test_`, `*Test.java` or `*.spec.ts`
    would be a gate that knows about languages. Item 40 settled that argument.
    Here the run says what its test is called, in whatever its language calls
    it, and the harness checks the name is really in the diff."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id, PATCH.replace("test_thing_refuses_when_not_ready",
                                     "ShortenerSpec#refusesWhenNotReady"))
    repro(repo, run_id, "Regression test: `ShortenerSpec#refusesWhenNotReady`.\n")
    assert D.obligations(repo, run_id) == []


def test_backticked_prose_is_not_read_as_a_test_name(repo):
    """A backticked phrase in a sentence is not a claim about a test. A check
    that demanded every backticked word appear in the diff would be unusable,
    and unusable checks get switched off.

    **The property held; the mechanism that held it did not, and a field run
    is what said so.** The first cut filtered by SHAPE - any backticked token
    that looked like an identifier was a claimed test name - and shape cannot
    tell a test from a filename, a commit sha or a run id. A reproduction
    written in this project's ordinary house style produced SEVEN false
    obligations, then an eighth from a backticked review-finding id.

    The cost was not noise. The only way past the check was to strip backticks
    off every legitimate cross-reference, so the check made documents WORSE -
    which is how an unusable check earns the switching off this docstring
    already warned about.

    So names are now read from the line that DECLARES them and nowhere else.
    The prose below is deliberately identifier-shaped and absent from the
    patch: under the old rule it was two false obligations, and under this one
    it is prose.
    """
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id,
          "It regressed the way `20260828-171253-e20217` did, and the fix is\n"
          "argued in `30-design.md` against the base commit `c3f39f401b`.\n"
          "Regression test: `test_thing_refuses_when_not_ready`.\n")
    assert D.named_tests("`a null value` and `test_x`") == []
    assert D.named_tests("Regression test: `test_x`") == ["test_x"]
    assert D.obligations(repo, run_id) == []


# ── absent is not clean, and there are two absences ─────────────────────────


def test_a_run_with_no_implementation_yet_owes_nothing_here(repo):
    """`stage.verify` already says P4 owes its artifact, by name. Saying it
    twice in different words teaches people to skim both."""
    run_id = open_run(repo, "defect")
    assert D.obligations(repo, run_id) == []


def test_an_implementation_with_no_diff_cannot_be_checked_and_says_so(repo):
    """The other absence, and it is this module's business: a delivered
    `40-impl/` with no patch in it means the check CANNOT LOOK, which is not
    the same as looking and finding nothing."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id, patch=None)
    repro(repo, run_id, GOOD)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1
    assert ST.CHANGE_PATCH in owed[0] and "not the same as verified clean" in owed[0]


def test_a_reproduction_that_is_not_recorded_is_not_pinned(repo):
    """Item 32's rule: an artifact nobody recorded has bytes nothing pins, so
    a yes about them covers nothing."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id, GOOD, record=False)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "never recorded" in owed[0]


def test_an_empty_reproduction_is_the_same_as_none(repo):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id, "   \n\n")
    owed = D.obligations(repo, run_id)
    assert owed and "absent or empty" in owed[0]


# ── wired into the phase contract, not merely available ─────────────────────


def test_the_phase_contract_owes_it_conditionally(repo):
    assert ST.REPRO in ST.by_phase("P4").conditional
    assert ST.CONDITIONAL_JUDGE["P4"][0] == "tools.sdlc.defect"


def test_verify_refuses_the_phase_while_the_reproduction_is_owed(repo):
    """The obligation has to reach the phase machinery, or it is a CLI nobody
    runs. `stage.verify` is what G3 stands on."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    L.record_artifact(repo, run_id, ST.IMPL_DIR)
    problems = ST.verify(repo, run_id, "P4")
    assert any(ST.REPRO in p for p in problems), problems


def test_verify_is_clean_once_the_reproduction_is_there(repo):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    L.record_artifact(repo, run_id, ST.IMPL_DIR)
    repro(repo, run_id, GOOD)
    assert ST.verify(repo, run_id, "P4") == []


def test_the_p4_prompt_tells_the_runner_the_checker_decides(repo):
    """Item 64's seam: whether a conditional artifact is owed is never the
    runner's opinion of its own work."""
    run_id = open_run(repo, "defect")
    text = ST.render_prompt(repo, run_id, "P4",
                            {"component": "api", "branch": "b"})
    assert ST.REPRO in text and "defect.py" in text


def test_the_p4_sop_states_what_a_defect_owes():
    text = ST.sop_path(ST.by_phase("P4"), ROOT).read_text(encoding="utf-8")
    assert ST.REPRO in text and ST.CHANGE_PATCH in text
    assert "defect.py --target <repo> --run <id> --check" in text


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_exits_one_while_something_is_owed(repo, capsys):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    assert D.main(["--target", str(repo), "--run", run_id, "--check"]) == 1
    assert ST.REPRO in capsys.readouterr().out


def test_the_cli_exits_zero_on_a_requirement(repo, capsys):
    run_id = open_run(repo, "requirement")
    impl(repo, run_id)
    assert D.main(["--target", str(repo), "--run", run_id, "--check"]) == 0
    assert "not a defect" in capsys.readouterr().out


def test_the_cli_reports_as_data(repo, capsys):
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id, GOOD)
    assert D.main(["--target", str(repo), "--run", run_id, "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["applies"] and found["obligations"] == []
    assert found["grounded"] == ["test_thing_refuses_when_not_ready"]


# ── the field run's finding: shape is not a declaration ─────────────────────


def test_only_the_declaring_line_names_a_regression_test(repo):
    """The fix for the finding this check earned against itself. A document may
    cite its siblings, its base commit and its own run id freely; none of them
    becomes an obligation."""
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id,
          "See `10-requirements.md` and `00-intake.json`, base `c3f39f401b`,\n"
          "run `20260828-171253-e20217`, finding `assertion-count-25-vs-21`.\n"
          "Regression test: `test_thing_refuses_when_not_ready`.\n")
    assert D.named_tests(repro_text(repo, run_id)) == [
        "test_thing_refuses_when_not_ready"]
    assert D.obligations(repo, run_id) == []


def test_a_heading_declares_them_too(repo):
    """Both forms, because both are how people really write it - a labelled
    line, and a heading followed by the name. The real reproduction this was
    found on used the heading form."""
    assert D.named_tests("## 2 · The regression test\n\n`test_x`\n") == ["test_x"]
    assert D.named_tests("Regression tests: `test_x`, `test_y`\n") == [
        "test_x", "test_y"]


def test_a_declaration_section_does_not_sweep_up_its_own_paragraph(repo):
    """Bounded at ONE following line on purpose. A section that scanned to the
    next heading would collect every backticked word in the paragraph, which is
    the defect being undone rather than a smaller version of it."""
    text = ("## The regression test\n\n`test_x`\n\n"
            "It replaces `assertEquals` and cites `30-design.md`.\n")
    assert D.named_tests(text) == ["test_x"]


def test_a_commit_sha_in_the_patch_header_does_not_ground_anything(repo):
    """`git format-patch` writes `From <sha>`, and a substring search over the
    whole patch grounded that sha as a regression test - the check satisfied by
    the patch's own plumbing. Grounding reads hunk CONTENT now."""
    run_id = open_run(repo, "defect")
    sha = "3a4274f125abcdef"
    patch = (f"From {sha} Mon Sep 17 00:00:00 2001\n"
             f"Subject: [PATCH] fix\n\n"
             "diff --git" + PATCH.split("diff --git", 1)[1])
    # The fixture has to exercise the property: the sha must really be IN the
    # patch, in its header, or this passes because there was nothing to find.
    # The first cut of this test stripped the header it was written to test.
    assert sha in patch and patch.startswith("From ")
    impl(repo, run_id, patch=patch)
    repro(repo, run_id, f"Regression test: `{sha}`.\n")
    owed = D.obligations(repo, run_id)
    assert owed and "does not contain it" in owed[0]
    assert sha not in D.hunks(patch)


def test_a_test_touched_as_context_is_still_grounded(repo):
    """Demanding a `+` line would be narrower than the truth: a test whose body
    changed but whose signature line did not is still a test this change
    touched. Context lines are inside the hunk and count."""
    patch = ("diff --git a/tests/t.py b/tests/t.py\n"
             "--- a/tests/t.py\n+++ b/tests/t.py\n"
             "@@ -1,4 +1,4 @@\n"
             " def test_unchanged_signature():\n"
             "-    assert old()\n"
             "+    assert new()\n")
    run_id = open_run(repo, "defect")
    impl(repo, run_id, patch=patch)
    repro(repo, run_id, "Regression test: `test_unchanged_signature`.\n")
    assert D.obligations(repo, run_id) == []


def test_a_later_files_header_is_metadata_too(repo):
    """**Found by a mutation that survived TWO attempts to kill it, and the
    second attempt was aimed at the wrong mechanism.**

    The hunk boundary is reset at `diff --git` and `From ` lines. The first
    test for it used a second commit's `From <sha>` header - which the
    character filter already excludes, because `From` starts with `F` and
    only `+`, `-` and space are collected. So that test passed with or
    without the reset, which is a test measuring nothing.

    The lines the reset actually discriminates are a LATER file's `---` and
    `+++` headers, which DO start with `-` and `+`. Without it they are
    collected as hunk content, and a path named in a reproduction grounds
    itself against the patch's own file list rather than against any change.
    """
    run_id = open_run(repo, "defect")
    patch = (
        "diff --git a/tests/t.py b/tests/t.py\n"
        "--- a/tests/t.py\n+++ b/tests/t.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/app/only_in_header.py b/app/only_in_header.py\n"
        "--- a/app/only_in_header.py\n+++ b/app/only_in_header.py\n"
        "@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    # The fixture must exercise the property: the name IS in the patch, and
    # only in file headers, so grounding it would be grounding on metadata.
    # Four: twice on the `diff --git` line, once on `---`, once on `+++`.
    assert patch.count("only_in_header") == 4
    impl(repo, run_id, patch=patch)
    repro(repo, run_id, "Regression test: `only_in_header`.\n")
    owed = D.obligations(repo, run_id)
    assert owed and "does not contain it" in owed[0]
    assert "only_in_header" not in D.hunks(patch)


def test_the_docstring_teaches_a_groundable_form_not_adr_s_file_convention(repo):
    """T150 / W63 A7b-4: the module docstring taught a form that can never ground.

    defect.py's docstring told a runner to name the regression test in backticks
    'the convention `adr.py` already uses for the files a design intends to
    change' - i.e. by its FILE. But `hunks` drops every file header, so a name
    that lives only in a path is never in the grounded body, and the P4 SOP
    instead teaches naming the test 'spelled exactly as the change spells it' (a
    function or `Class#method`), grounded 'in the changed content, not the
    patch's headers'. The docstring was the single reader out of step.

    Two welded halves:
      * behavioural (green on both source states) - the FILE-PATH form the old
        docstring pointed at is REFUSED by the real grounder while the
        identifier form grounds, which is the 'path case' T150 found untested;
      * doc-consistency (red on the pre-fix docstring) - the docstring no longer
        routes regression-test naming through adr.py's file convention.
    """
    # A real add-a-test patch: the file path lives only in the headers hunks()
    # drops; the test's identifier lives in the changed content.
    patch = (
        "diff --git a/tests/test_shorten.py b/tests/test_shorten.py\n"
        "--- /dev/null\n+++ b/tests/test_shorten.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def test_refuses_when_not_ready():\n"
        "+    with pytest.raises(ValueError):\n"
        "+        shorten()\n")
    body = D.hunks(patch)
    assert "tests/test_shorten.py" not in body, "the path is header-only"
    assert "test_refuses_when_not_ready" in body

    run_id = open_run(repo, "defect")
    impl(repo, run_id, patch=patch)

    # Named the adr.py way - by the FILE the change touches - and it is refused,
    # because the path never reaches the grounded content.
    repro(repo, run_id, "Regression test: `tests/test_shorten.py`.\n")
    owed = D.obligations(repo, run_id)
    assert owed and "does not contain it" in owed[0], owed

    # Named the SOP way - the identifier the change spells - and it grounds.
    repro(repo, run_id, "Regression test: `test_refuses_when_not_ready`.\n")
    assert D.obligations(repo, run_id) == []

    # The docstring must teach what can ground, not adr.py's FILE convention.
    doc = D.__doc__
    assert "files a design intends to change" not in doc, (
        "docstring still routes regression-test naming through adr.py's file "
        "convention, which hunks() can never ground")
    assert "never the file it lives in" in doc, (
        "docstring must teach naming the test by the identifier the change "
        "spells, the way the P4 SOP does, not by the file it lives in")
