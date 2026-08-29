"""The supply-chain quarantine — `sca_known_findings`.

The harness has always had a NAMED quarantine for the test gate: `known_failures`
lists pre-existing reds, so *"no new failures"* can be judged and a SECOND
failure appearing beside a declared one is still new. The supply-chain gate had
nothing of the kind — no waiver, no path scope, no accepted list — so a
repository carrying pre-existing CVEs had exactly one way past P5, which was to
force it.

**Forcing is indistinguishable from the thing the gate exists to catch.** A run
forced past 103 findings and a run that has just introduced a critical CVE
produce the same override in the same ledger, and once forcing is routine nobody
reads either. That is measured, not hypothetical: it happened on a live run on
2026-08-29, which is what commissioned this.

Two properties are worth more than the rest of this file, and both are about
what the declaration must NOT be able to do:

**A finding nobody declared still blocks.** This is the whole of it. A
quarantine that could be written loosely enough to cover future findings would
be an off switch with extra steps, so the identity is exact and includes the
VERSION: bump the package and the declaration stops matching, which is the
moment somebody should look again.

**A declaration that outlives its finding is reported.** The standing exception
nobody re-examines is the failure mode of every quarantine ever written,
including the one this mechanism is modelled on.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import sca as SCA  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import suite as ENGINE  # noqa: E402

#: One osv-scanner group, with the shape that makes this mechanism necessary:
#: `ids[0]` is a PYSEC id and the human writing a declaration has the CVE.
GROUP = {"ids": ["PYSEC-2018-28", "GHSA-x84v-xcm2-53pg"],
         "aliases": ["CVE-2018-18074", "GHSA-x84v-xcm2-53pg", "PYSEC-2018-28"],
         "max_severity": "9.1"}

#: A second advisory against the SAME package version. This is the fixture the
#: key property is proved with: declaring the first must not cover this one.
OTHER = {"ids": ["PYSEC-2099-01"], "aliases": ["CVE-2099-0001"],
         "max_severity": "9.8"}

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


def report(groups=(GROUP,), name="requests", version="2.19.1",
           ecosystem="PyPI") -> dict:
    return {"results": [{
        "source": {"path": "/scan/api/requirements.txt", "type": "lockfile"},
        "packages": [{
            "package": {"name": name, "version": version, "ecosystem": ecosystem},
            "groups": list(groups)}]}]}


def diags(groups=(GROUP,), **kw) -> list[dict]:
    return SCA.parse(report(groups=groups, **kw), Path("/scan"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def declare(repo: Path, entries: list[str] | None, key: str = SCA.QUARANTINE_KEY):
    """Write a quarantine into the profile's TOP LEVEL, which is the only place
    a single-component install can reach — the shape that motivated this."""
    text = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    if entries is not None:
        rendered = ", ".join(f'"{e}"' for e in entries)
        text += f"{key}: [{rendered}]\n"
    (repo / ".sdlc" / "profile.yml").write_text(text, encoding="utf-8")


def record(repo: Path, run_id: str, rows: list[dict]) -> None:
    """An SCA document carrying `rows`, built through the real writer."""
    named = [{"name": "all"}]
    doc = SCA._document(
        repo,
        [{"name": SCA.TOOL, "status": "ok", "detail": "", "findings": len(rows),
          "components": ["all"]}],
        [{"source": {"name": SCA.TOOL}, "diagnostics": rows}],
        named)
    path = ledger.run_dir(repo, run_id) / SCA.RESULT_NAME
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    ledger.record_artifact(repo, run_id, SCA.RESULT_NAME)


def open_run(repo: Path) -> str:
    return ledger.create(repo, "defect", "a title")["run_id"]


ENTRY = "PyPI requests 2.19.1 CVE-2018-18074"


# ── identity: what a declaration is allowed to name ─────────────────────────


def test_a_finding_carries_the_identity_a_declaration_names():
    row = diags()[0]
    assert SCA.coordinate(row) == ("PyPI", "requests", "2.19.1")
    assert "CVE-2018-18074" in SCA.names_of(row)


def test_an_entry_may_be_spelled_with_the_cve_not_only_the_first_id():
    """The reason `names_of` exists. OSV lists whichever database it likes
    first — on a real estate regularly a `BIT-` id — while the person writing
    the declaration is reading a CVE. A mechanism that could only be spelled
    with `ids[0]` is one nobody can use."""
    row = diags()[0]
    assert row["code"]["value"] == "PYSEC-2018-28"          # not the CVE
    assert SCA.matches("PyPI requests 2.19.1 CVE-2018-18074", row)
    assert SCA.matches("PyPI requests 2.19.1 PYSEC-2018-28", row)


def test_the_proposed_entry_prefers_the_cve():
    assert SCA.entry_for(diags()[0]) == ENTRY


def test_a_finding_with_no_identity_matches_nothing():
    """Absent is not a match. A document written before the identity existed,
    or by something that is not this module, must keep BLOCKING rather than be
    quarantined by an entry it cannot be compared against."""
    row = diags()[0]
    del row[SCA.QUARANTINE_FIELD]
    assert SCA.coordinate(row) is None
    assert not SCA.matches(ENTRY, row)


# ── the property the mechanism lives or dies by ─────────────────────────────


def test_a_different_advisory_in_the_same_package_still_blocks(repo):
    """**The key property.** Declaring one CVE must not accept the next one
    published against the same dependency. If this ever passes vacuously the
    quarantine has become an off switch."""
    run_id = open_run(repo)
    declare(repo, [ENTRY])
    record(repo, run_id, diags(groups=(GROUP, OTHER)))
    owed = ENGINE.sca_problems(repo, run_id)
    assert any("CVE-2099-0001" in p for p in owed), owed
    assert not any("CVE-2018-18074" in p for p in owed), owed


def test_a_declared_finding_is_not_owed(repo):
    run_id = open_run(repo)
    declare(repo, [ENTRY])
    record(repo, run_id, diags())
    assert ENGINE.sca_problems(repo, run_id) == []


def test_an_undeclared_finding_is_owed(repo):
    run_id = open_run(repo)
    declare(repo, None)
    record(repo, run_id, diags())
    owed = ENGINE.sca_problems(repo, run_id)
    assert len(owed) == 1 and "CVE-2018-18074" in owed[0]


def test_a_version_bump_invalidates_the_declaration(repo):
    """Deliberate, and the sharpest edge of the design. A quarantine that
    survived a version bump would be standing over a dependency nobody has
    looked at since it was written."""
    run_id = open_run(repo)
    declare(repo, [ENTRY])
    record(repo, run_id, diags(version="2.20.0"))
    owed = ENGINE.sca_problems(repo, run_id)
    assert any("CVE-2018-18074" in p for p in owed), owed


def test_a_declaration_for_another_package_does_not_reach_this_one(repo):
    run_id = open_run(repo)
    declare(repo, ["PyPI urllib3 2.19.1 CVE-2018-18074"])
    record(repo, run_id, diags())
    assert any("CVE-2018-18074" in p for p in ENGINE.sca_problems(repo, run_id))


# ── a quarantine that outlives its finding ──────────────────────────────────


def test_a_stale_entry_is_reported(repo):
    run_id = open_run(repo)
    declare(repo, [ENTRY, "PyPI urllib3 1.0.0 CVE-2099-9999"])
    record(repo, run_id, diags())
    owed = ENGINE.sca_problems(repo, run_id)
    assert len(owed) == 1 and "matched nothing" in owed[0]
    assert "CVE-2099-9999" in owed[0]


def test_a_malformed_entry_is_reported_rather_than_ignored(repo):
    """An entry nobody can match looks exactly like one that matched. Silence
    about it is silence about whichever vulnerability it was meant to name."""
    run_id = open_run(repo)
    declare(repo, ["CVE-2018-18074"])
    record(repo, run_id, diags())
    owed = ENGINE.sca_problems(repo, run_id)
    assert any("is not 4 tokens" in p for p in owed), owed
    assert any("CVE-2018-18074" in p and "PyPI" in p for p in owed), owed


# ── reached from a single-component install, which is the motivating shape ──


def test_the_top_level_key_reaches_a_single_component_install(repo):
    """A single-repository install declares no `components:`, so the component
    `policy.components` synthesises carries name/path/role/vcs and nothing
    else. `known_failures` was UNREACHABLE there once, for exactly this
    reason, and no repository with a pre-existing failure could leave P5."""
    from tools.sdlc import policy as guardrails
    declare(repo, [ENTRY])
    comp = guardrails.component(repo, guardrails.components(repo)[0]["name"])
    assert ENGINE.quarantined(repo, comp, SCA.QUARANTINE_KEY) == [ENTRY]


def test_an_absent_declaration_is_an_empty_quarantine_not_one_named_none(repo):
    """`_flow_list(None)` answers `["None"]` — one declared entry named None.
    Absent is not a declaration."""
    from tools.sdlc import policy as guardrails
    declare(repo, None)
    comp = guardrails.component(repo, guardrails.components(repo)[0]["name"])
    assert ENGINE.quarantined(repo, comp, SCA.QUARANTINE_KEY) == []


def test_both_quarantines_go_through_one_reader(repo):
    """The two keys are read by one function, so a lesson learned about either
    cannot be learned for only one of them. This is the class that produced
    the bug the reader's own docstring describes."""
    declare(repo, [ENTRY])
    declare(repo, ["test_a"], key=ENGINE.KNOWN_FAILURES)
    from tools.sdlc import policy as guardrails
    got = ENGINE.declared(repo, guardrails.components(repo)[0]["name"])
    assert got[ENGINE.KNOWN_FAILURES] == ["test_a"]
    assert got[SCA.QUARANTINE_KEY] == [ENTRY]


# ── the draft, because 67 entries by hand is a check that gets switched off ──


def test_propose_drafts_an_entry_per_finding_and_decides_nothing():
    doc = SCA._document(Path("."), [], [{"source": {"name": SCA.TOOL},
                                         "diagnostics": diags((GROUP, OTHER))}],
                        [{"name": "all"}])
    text = SCA.propose(doc)
    assert ENTRY in text and "PyPI requests 2.19.1 CVE-2099-0001" in text
    assert "decision nobody has taken yet" in text
    assert SCA.QUARANTINE_KEY + ":" in text


def test_propose_deduplicates_because_identity_carries_no_path():
    """One CVE in one package version is ONE decision however many manifests
    resolve it — measured at 85 findings for 67 decisions on the repository
    that motivated this."""
    rows = diags() + diags()
    doc = SCA._document(Path("."), [], [{"source": {"name": SCA.TOOL},
                                         "diagnostics": rows}],
                        [{"name": "all"}])
    assert SCA.propose(doc).count(ENTRY) == 1


def test_propose_writes_nothing_to_the_profile(repo, capsys):
    """A tool that quarantined its own findings would be a gate marking its own
    homework. It prints; a person pastes."""
    before = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    doc = SCA._document(repo, [], [{"source": {"name": SCA.TOOL},
                                    "diagnostics": diags()}], [{"name": "all"}])
    SCA.propose(doc)
    assert (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8") == before


def test_a_blank_identity_is_unnameable_rather_than_proposed_as_garbage():
    """Present-but-empty is INVALID, and invalid is not absent and not valid.

    A finding whose identity fields are blank must not be turned into a
    declaration: `entry_for` would render `"   CVE-x"`, which strips to a
    one-token entry that `matches` can never match and `malformed` would then
    report forever. Found by a mutation that survived every other test in this
    file — `matches` returns False either way, so only the DRAFT can see it.
    """
    row = diags()[0]
    row[SCA.QUARANTINE_FIELD] = {"ecosystem": "", "package": "", "version": "",
                                 "ids": ["PYSEC-1"], "aliases": ["CVE-1"]}
    assert SCA.coordinate(row) is None
    assert SCA.entry_for(row) == ""
    doc = SCA._document(Path("."), [], [{"source": {"name": SCA.TOOL},
                                         "diagnostics": [row]}],
                        [{"name": "all"}])
    text = SCA.propose(doc)
    assert "carried no identity" in text
    assert "CVE-1" not in text


# ── zero silent drops: what the declaration accepted is counted ─────────────


def test_what_the_quarantine_silenced_is_counted_and_nameable(repo):
    """The other half of the mechanism. A gate that quietly stops reporting is
    the worst thing in this codebase's book, and a declaration is a licence to
    stop reporting. So `sca_problems` says what is OWED and this says what was
    ACCEPTED — never the same list, never the same question."""
    run_id = open_run(repo)
    declare(repo, [ENTRY])
    record(repo, run_id, diags(groups=(GROUP, OTHER)))

    assert ENGINE.sca_problems(repo, run_id) == [
        p for p in ENGINE.sca_problems(repo, run_id) if "CVE-2099-0001" in p]

    said = ENGINE.sca_silenced(repo, run_id)
    assert said["quarantined"] == 1
    assert said["advisories"] == [ENTRY]
    assert said["declared"] == [ENTRY]
    assert said["stale"] == []


def test_nothing_declared_means_nothing_silenced(repo):
    """Zero here must be a real zero, not the absence of an answer."""
    run_id = open_run(repo)
    declare(repo, None)
    record(repo, run_id, diags())
    said = ENGINE.sca_silenced(repo, run_id)
    assert said == {"declared": [], "quarantined": 0, "advisories": [],
                    "stale": []}


def test_a_stale_entry_shows_up_in_both_places(repo):
    """It is owed as a problem AND visible in the accounting, because the two
    are read by different people at different moments."""
    run_id = open_run(repo)
    declare(repo, ["PyPI urllib3 1.0.0 CVE-2099-9999"])
    record(repo, run_id, diags())
    assert any("matched nothing" in p for p in ENGINE.sca_problems(repo, run_id))
    assert ENGINE.sca_silenced(repo, run_id)["stale"] == [
        "PyPI urllib3 1.0.0 CVE-2099-9999"]
