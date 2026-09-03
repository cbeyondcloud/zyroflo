"""Item 124 - the walk's observation tail: two mechanisms and two rulings.

Item 114's walk recorded four things that are not defects. Two became work and
two became written rulings, because an observation whose honest answer is *this
is the finest thing possible here, and here is why* is worth more written down
than built badly.

O4  A release cut named a version nothing verified the artifact carries: the tag
    and changelog said 2.9.4 and the deployed jar was 2.9.3-SNAPSHOT.
O3  The context pack landed in a committed run folder, though `pack.py` states
    in its own help that packs belong in scratch and never in git.
O1  ADR triggers are FILE-granular - ruled, not built. At P3 there is no diff.
O2  Nothing checks two acceptance criteria against each other - ruled, not
    built. That is prose against prose, which item 99 already declined.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.graph.outdir import (  # noqa: E402
    OWN_ARTIFACT_DIR, SCRATCH_DIR)
from tools.sdlc import adr as A  # noqa: E402
from tools.sdlc import claims as C  # noqa: E402
from tools.sdlc import release as R  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402


def _git(where: Path, *args: str):
    return subprocess.run(["git", "-C", str(where), *args],
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: repo" + chr(10) + "gate_profile: solo" + chr(10)
        + 'test_command: "pytest -q"' + chr(10), encoding="utf-8")
    (root / "f.txt").write_text("x" + chr(10), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    # A LOCAL identity, not an inline one. The release cut's default tag step is
    # `git tag -a`, which needs one - and passing it inline for the commit alone
    # made this fixture borrow the MACHINE's global config. It passed here and
    # failed on CI, which is item 122's environment-dependent fixture again.
    _git(root, "config", "user.email", "d@x.invalid")
    _git(root, "config", "user.name", "D")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _release(repo: Path, **dials: str) -> None:
    body = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    body += "release:" + chr(10)
    for key, value in dials.items():
        body += '  %s: "%s"%s' % (key, value, chr(10))
    (repo / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")


def _run(repo: Path) -> str:
    st = L.create(repo, "requirement", "a change")
    return st["run_id"]


# ── O3: a pack belongs in scratch, and the run folder is committed ──────────


def test_the_pack_is_written_to_the_gitignored_scratch_directory(repo):
    """**O3.** `pack.py --out` states that packs are derived artifacts and
    belong in scratch, never in git - and since item 121 the run folder is
    COMMITTED with the code it describes, so a pack written there ships to
    everyone who clones.

    **Item 131 moved the concrete path** - `<target>/.tmp/` was NOT the tree
    the installer ignores (only `<target>/.sdlc/.tmp/` is), so this test's own
    assertion of `repo / SCRATCH_DIR` was pinning the very defect item 131
    fixed. The PROPERTY here is unchanged: inside the harness's own directory,
    never inside the committed run folder.
    """
    rid = _run(repo)
    where = ST._pack_out_for(repo, rid)
    assert Path(where).is_relative_to(repo / OWN_ARTIFACT_DIR / SCRATCH_DIR), where
    assert rid in where
    assert not Path(where).is_relative_to(L.runs_dir(repo)), (
        "the pack is inside the run folder, which is committed")


def test_supplying_the_pack_directory_is_refused_rather_than_ignored(repo):
    """Item 109's other half, for the third value after `worktree` and
    `baseline`: a supplied value nothing reads is the same defect as a value
    nobody supplies, arriving from the other side."""
    rid = _run(repo)
    for phase in ("P2", "P6"):
        with pytest.raises(ST.StageError, match="resolves pack-out from the run"):
            ST.resolve(repo, ST.by_phase(phase), rid, {"pack-out": "/anywhere"})


def test_both_phases_that_build_a_pack_resolve_it_the_same_way():
    """P2 and P6 both write one. A value that were run-sourced at one and
    caller-supplied at the other is the two-channel split item 109 closed."""
    for phase in ("P2", "P6"):
        packs = [v for v in ST.by_phase(phase).values if v.key == "pack-out"]
        assert packs and packs[0].source == "run", phase


def test_prepass_output_stays_a_callers_value_and_that_is_deliberate():
    """The discrimination, stated where it can fail. `prepass.json` and
    `prepass.md` are P6's declared EVIDENCE in the stage contract - they belong
    in the record. A pack does not, which is the whole of O3."""
    p6 = ST.by_phase("P6")
    assert ST.PREPASS_RESULT in p6.evidence and ST.PREPASS_REPORT in p6.evidence
    assert [v.source for v in p6.values if v.key == "prepass-out"] == ["caller"]
    assert "pack" not in " ".join(p6.evidence)


def test_the_sops_stop_asking_a_runner_for_the_pack_directory():
    """A reader enforcing something the procedure still contradicts is item
    33's class - the SOPs told a runner to `--set pack-out=<dir>`."""
    for name in ("P2-scoping.md", "P6-review.md"):
        text = (ST.HARNESS / ST.SOP_DIR / name).read_text(encoding="utf-8")
        # The COMMAND LINE, not the whole document: the SOP now explains that
        # `--set pack-out=` is refused, so a substring ban would fire on the
        # sentence saying so - item 47's `gh pr merge` ban failing on a section
        # header, and item 94's docstring exemption, for the third time.
        rendered = [l for l in text.splitlines() if l.startswith("stage.py ")]
        assert rendered, name
        assert not any("--set pack-out=" in l for l in rendered), name
        assert "stated by the harness, not supplied" in text, name


# ── O4: the version the ARTIFACT carries ────────────────────────────────────


def test_a_declared_command_that_disagrees_is_a_finding_not_a_warning(repo):
    """**O4, and the walk's exact case.** The cut names 2.9.4 and the artifact
    carries 2.9.3-SNAPSHOT, which is a false claim other people consume."""
    said = R.artifact_version(repo, _run(repo), "2.9.4")
    assert said["checked"] is False          # nothing declared yet
    _release(repo, artifact_version_command="echo 2.9.3-SNAPSHOT")
    said = R.artifact_version(repo, _run(repo), "2.9.4")
    assert said["checked"] is True and said["agrees"] is False
    assert "2.9.4" in said["why"] and "2.9.3-SNAPSHOT" in said["why"]


def test_agreement_is_recorded_as_agreement(repo):
    """The control. A check that can only fail is one nobody keeps."""
    _release(repo, artifact_version_command="echo 2.9.4")
    said = R.artifact_version(repo, _run(repo), "2.9.4")
    assert said["checked"] is True and said["agrees"] is True


def test_a_leading_v_is_the_same_version(repo):
    """`v2.9.4` and `2.9.4` are one version written two ways, and refusing a
    release over a prefix would be this check inventing a finding."""
    _release(repo, artifact_version_command="echo v2.9.4")
    assert R.artifact_version(repo, _run(repo), "2.9.4")["agrees"] is True


def test_no_declaration_records_the_version_as_UNVERIFIED(repo):
    """**What keeps this from being the eighth dead dial.** Blank is not
    silence: its absence is reported every run, which is item 65's *a skipped
    step is RECORDED, not absent* and item 108's third channel."""
    said = R.artifact_version(repo, _run(repo), "2.9.4")
    assert said["checked"] is False and said["agrees"] is None
    assert "UNVERIFIED" in said["why"] and "artifact_version_command" in said["why"]


@pytest.mark.parametrize("command,why", [
    ("exit 3", "could not look"),
    ("echo nothing version-shaped here", "named no version"),
])
def test_could_not_look_is_never_agreement(repo, command, why):
    """A command that fails, or answers without naming a version, leaves
    `agrees` as None. Folding either into agreement is the distinction this
    package keeps having to make."""
    _release(repo, artifact_version_command=command)
    said = R.artifact_version(repo, _run(repo), "2.9.4")
    assert said["checked"] is False and said["agrees"] is None
    assert why in said["why"]


def test_nothing_to_check_against_is_not_a_failure(repo):
    """When no version could be determined there is nothing to compare, and
    reporting that as a mismatch would blame the artifact for the bump."""
    _release(repo, artifact_version_command="echo 2.9.4")
    said = R.artifact_version(repo, _run(repo), "")
    assert said["checked"] is False and said["agrees"] is None
    assert "nothing to check the artifact against" in said["why"]


def test_the_dial_is_not_a_cut_step():
    """It CHECKS a name; the steps MINT one. Folding it into `CUT_STEPS` would
    make a verification look like another way of producing the thing it
    verifies."""
    assert R.ARTIFACT_VERSION_DIAL not in [f"{s}_command" for s in R.CUT_STEPS]


def test_the_dial_avoids_the_word_P10_already_owns():
    """`verify` means *ask the running service* since item 48. A second meaning
    of one word is item 36's vocabulary gap, which once let a leaked credential
    exit a review loop clean."""
    assert "verify" not in R.ARTIFACT_VERSION_DIAL
    assert R.ARTIFACT_VERSION_DIAL == "artifact_version_command"


def test_the_shipped_template_documents_the_dial():
    """A dial nobody is told about is the dead-config class this project has
    found seven times."""
    text = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    assert "artifact_version_command" in text
    assert "BLANK IS NOT SILENT" in text


# ── O1 and O2: ruled, not built ─────────────────────────────────────────────


def test_the_file_granular_trigger_is_ruled_with_its_reason():
    """**O1.** Measured: P3 produces the design and the change first exists at
    P4, so at the phase where a trigger must fire there is no diff and *a file
    the design names* is the finest resolution there is."""
    assert ST.by_phase("P3").produces == ("30-design.md",)
    assert any("change.patch" in a or "40-impl" in a
               for a in ST.by_phase("P4").produces)
    # Whitespace-normalised: a test that depends on where a paragraph WRAPS is
    # asserting about the formatter, not about the ruling.
    said = " ".join(A.__doc__.split())
    assert "FILE-granular" in said
    assert "there is no diff" in said
    assert "what would change the ruling" in said.lower()


def test_criteria_against_each_other_is_declined_with_its_reason():
    """**O2.** Item 99's scope is narrow on purpose - a claim against a number
    the HARNESS measured - and this is that same refusal one step out."""
    said = " ".join(C.__doc__.split())
    assert "Considered and declined" in said
    assert "prose against prose" in said
    assert "what would change the ruling" in said.lower()


def test_a_ruling_names_what_would_overturn_it():
    """Both rulings say what would change them. A decision recorded without its
    reversal condition is one the next person has to re-derive."""
    for doc in (A.__doc__, C.__doc__):
        assert "would change the ruling" in " ".join(doc.split()).lower()


# ── composed, because the mutation pass found FOUR gaps of one class ────────
#
# Every test above exercises a piece. Four mutations survived them all - the
# resolver never putting the pack in the prompt, the cut never asking the
# artifact, the cut never recording the answer, and a disagreement never
# reaching P8's obligations - because nothing ran the piece THROUGH the thing
# that uses it. This project's most-found class, four times in one item.


def _version_findings(repo: Path, rid: str) -> list[str]:
    """Only the obligations ABOUT the artifact's version.

    P8 owes a CI verdict too, so `obligations() == []` would be asserting
    something else entirely - and would have passed or failed for reasons
    nothing to do with this item.
    """
    return [o for o in R.obligations(repo, rid) if "the artifact carries" in o]


def _at_p8(repo: Path) -> str:
    rid = _run(repo)
    L.advance(repo, rid, phase="P8", status="running")
    return rid


def test_the_resolver_puts_the_scratch_pack_into_the_prompt(repo):
    """`_pack_out_for` is tested and `resolve` is tested; this composes them.
    A resolver that computed the right path and handed over a blank would pass
    every other test in this file."""
    rid = _run(repo)
    for phase in ("P2", "P6"):
        values = ST.resolve(repo, ST.by_phase(phase), rid, {
            "diff": "d.patch", "component": "core", "prepass-out": "o",
            "hitl-target": str(repo)} if phase == "P6" else {"component": "core"})
        assert values["pack-out"] == ST._pack_out_for(repo, rid), phase
        assert values["pack-out"], phase


def test_the_cut_asks_the_artifact_and_records_what_it_said(repo):
    """**The cut is where this has to happen**, and a check that runs only when
    a test calls it directly is a check the phase never performs."""
    _release(repo, version_command="echo 2.9.4",
             artifact_version_command="echo 2.9.3-SNAPSHOT")
    rid = _at_p8(repo)
    row = R.cut(repo, rid)
    said = row["artifact_version"]
    assert said["checked"] is True and said["agrees"] is False
    assert said["found"] == "2.9.3-SNAPSHOT"

    # and it is in the DOCUMENT, not only the return value
    doc = json.loads((L.run_dir(repo, rid) / R.CUT_RESULT)
                     .read_text(encoding="utf-8"))
    assert doc["artifact_version"]["agrees"] is False


def test_a_disagreeing_artifact_is_owed_at_p8(repo):
    """**The finding reaches the gate.** `obligations` is what `stage.verify`
    asks, so a mismatch that stopped there would be a record nobody acts on -
    the dead-channel class this project keeps finding."""
    _release(repo, version_command="echo 2.9.4",
             artifact_version_command="echo 2.9.3-SNAPSHOT")
    rid = _at_p8(repo)
    row = R.cut(repo, rid)
    # Named, so a cut that did not complete says so instead of showing an empty
    # list: `obligations` reports the incomplete cut FIRST and the version
    # finding never gets a chance, which is what an empty list looked like.
    assert row["cut"] is True, [s for s in row["steps"]
                                if s.get("exit_code") not in (0, None)]
    owed = _version_findings(repo, rid)
    assert owed and "2.9.3-SNAPSHOT" in owed[0] and "2.9.4" in owed[0], owed


def test_an_agreeing_artifact_owes_nothing(repo):
    """The control. A check that can only add findings is one somebody
    removes."""
    _release(repo, version_command="echo 2.9.4",
             artifact_version_command="echo 2.9.4")
    rid = _at_p8(repo)
    R.cut(repo, rid)
    assert _version_findings(repo, rid) == []


def test_could_not_look_owes_nothing_and_is_not_agreement(repo):
    """**Proven rather than assumed.** Dropping the `checked` guard from the
    obligation is an EQUIVALENT mutation today - `agrees` is None whenever
    `checked` is False, and `None is False` is False either way - so this pins
    the property from the outside: an unanswerable check blocks nothing, and it
    is still not recorded as agreement."""
    _release(repo, version_command="echo 2.9.4",
             artifact_version_command="exit 3")
    rid = _at_p8(repo)
    row = R.cut(repo, rid)
    assert row["artifact_version"]["agrees"] is None
    assert row["artifact_version"]["checked"] is False
    assert _version_findings(repo, rid) == []


def test_a_blank_dial_never_reads_as_a_checked_version(repo):
    """The unverified sentence must not be sayable as reassurance. A mutation
    that kept the word UNVERIFIED while changing the rest of the sentence to
    *the version is fine* survived the first pass, which is a test asserting a
    word rather than a claim."""
    _release(repo, version_command="echo 2.9.4")
    rid = _at_p8(repo)
    said = R.cut(repo, rid)["artifact_version"]
    assert said["checked"] is False and said["agrees"] is None
    assert "UNVERIFIED" in said["why"]
    assert "nothing asked the built artifact" in said["why"]
    for reassurance in ("is fine", "is correct", "verified the", "agrees"):
        assert reassurance not in said["why"], said["why"]
    assert _version_findings(repo, rid) == []
