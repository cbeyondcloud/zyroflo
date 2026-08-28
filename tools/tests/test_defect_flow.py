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

    The phrases here are chosen to be absent from the patch AND to carry a
    space, so they fail the name filter for a reason the filter states. The
    first cut used `None` and `ValueError` - both `\\w+`, and both present in
    the patch - so it passed whether the filter worked or not, and a mutation
    that accepted any backticked word survived it. A fixture has to exercise
    the property its test is named for.
    """
    run_id = open_run(repo, "defect")
    impl(repo, run_id)
    repro(repo, run_id,
          "The call returned `a null value` where it should refuse, and "
          "`the calling controller` handed that to the client.\n"
          "Regression test: `test_thing_refuses_when_not_ready`.\n")
    assert D.named_tests("`a null value` and `test_x`") == ["test_x"]
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
