"""T712 (1) - the approver an entry records is the approver that was judged.

`append_entry` admitted an approver by `known_identity` (stripped) but recorded the raw
value, so a padded approver wrote `"  lead@...  "` into the hashed body and the
`gate_decided` event, and `metrics.py --export` under `identity: hash` gave one person
two `person-...` tokens. `record`, called as a library, guarded with `if not approver:`,
so a whitespace or non-string approver was accepted - a `forbid` claim nothing can judge
(its CLI door, `who()`, already strips). Now both doors admit AND record by
`known_identity`, and the export pseudonymises the identity the judge reads, so an
existing padded entry exports as the same person too - with its ledger and events bytes
never touched.

`append_entry` and `record`-as-a-library have no CLI, so each test writes in-process and
then SPAWNS `approve.py --verify` or `metrics.py --export` for what a person sees. Every
test fails on the pre-T712 code except the one named `test_BACK_COMPAT_*`, which
documents the must-not-change invariant.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests.test_t707_ledger_author import (  # noqa: E402,F401 - fixtures
    BLANK, DEV, LEAD, _handoff, _run_at, _spawn, forbid, repo, verify)

METRICS = ROOT / "tools" / "sdlc" / "metrics.py"

#: An `append_entry` entry with a padded approver and its `gate_decided` event, as the
#: PRE-T712 code wrote them (HEAD of main before this row): `author=DEV`,
#: `self_approval="forbid"`, run id and both clocks fixed. Captured, not computed - the
#: bytes an existing ledger holds, which T712 must neither rewrite nor fail.
PRE_T712_APPROVALS = (
    '{"approver": "  lead@example.invalid  ", "artifacts": {"handoffs/design-1.md": '
    '"sha256:4b77879b2e269a4972b73cc8c461208a5c44b0447587df7c1f0162d852762963"}, '
    '"at": "2026-11-07T09:00:00+00:00", "author": "dev@example.invalid", '
    '"author_source": "flag", "gate": "design-approval", "hash": '
    '"sha256:d99e7d02376c0971fecb42685bb5a14418becd46061db9bce5977014e3d0df03", '
    '"kind": "approval", "notes": "pre-T712", "prev": "", "run_id": "r-t712-pre", '
    '"schema_version": 1, "self_approval": "forbid", "verdict": "approve"}\n')
PRE_T712_EVENTS = (
    '{"at": "2026-11-08T09:00:00.000+00:00", "event": "run_opened", "hash": '
    '"sha256:af9a294e65c670ea771ac3e2d205cf104043c5a3b5c0fba8fbd8b14ad4a0a6d9", '
    '"kind": "requirement", "prev": "", "run_id": "r-t712-pre", "schema_version": 1, '
    '"seq": 1, "step": "P0", "step_name": "intake"}\n'
    '{"actor": "driving-tool", "at": "2026-11-08T09:00:00.000+00:00", "attempt": 1, '
    '"event": "step_started", "hash": '
    '"sha256:f0b07b5f12c6b70baa6b0ec502b8fc2fbf5ac358250344a3354b869811b0189d", "prev": '
    '"sha256:af9a294e65c670ea771ac3e2d205cf104043c5a3b5c0fba8fbd8b14ad4a0a6d9", '
    '"run_id": "r-t712-pre", "schema_version": 1, "seq": 2, "step": "P0", '
    '"step_name": "intake"}\n'
    '{"approver": "  lead@example.invalid  ", "at": "2026-11-08T09:00:00.000+00:00", '
    '"decision": "approve", "event": "gate_decided", "forced": false, "gate": '
    '"design-approval", "hash": '
    '"sha256:50e67dda123290fde4cdf8cd1dd7a05d604e9dd5021a76b04ee271c023b124ea", '
    '"ledger_hash": '
    '"sha256:d99e7d02376c0971fecb42685bb5a14418becd46061db9bce5977014e3d0df03", "prev": '
    '"sha256:f0b07b5f12c6b70baa6b0ec502b8fc2fbf5ac358250344a3354b869811b0189d", '
    '"run_id": "r-t712-pre", "schema_version": 1, "seq": 3, "step": "", '
    '"step_name": "", "waited_ms": null}\n')


def _hash_identity(repo: Path, extra: bytes = b"") -> None:
    (repo / ".sdlc" / "policy.yml").write_bytes(b"identity: hash\n" + extra)


def _export(repo: Path, out: Path) -> subprocess.CompletedProcess:
    return _spawn([str(METRICS), "--target", str(repo), "--export", "ndjson",
                   "--out", str(out)])


def _tokens(out: Path) -> set[str]:
    """Every approver the export wrote into its `gate_decided` files."""
    rows = [json.loads(line) for f in sorted(out.rglob("*.ndjson"))
            if f.parent.parent.name == "gate_decided"
            for line in f.read_text(encoding="utf-8").splitlines() if line]
    assert rows, f"the export wrote no gate_decided rows under {out}"
    return {r["approver"] for r in rows}


def _bytes(repo: Path, rid: str) -> tuple[bytes, ...]:
    return tuple(p.read_bytes() if p.exists() else b""
                 for p in (A.approvals_path(repo, rid), E.events_path(repo, rid),
                           L.run_dir(repo, rid) / L.STATE_NAME))


def _pre_t712_run(repo: Path) -> str:
    """A run whose ledger and events are the captured pre-T712 bytes."""
    rid, _ = _handoff(repo, "r-t712-pre")
    A.approvals_path(repo, rid).write_bytes(PRE_T712_APPROVALS.encode("utf-8"))
    E.events_path(repo, rid).write_bytes(PRE_T712_EVENTS.encode("utf-8"))
    return rid


# ── 1: append_entry records the approver it judged ───────────────────────────


@pytest.mark.parametrize("pad", ["  ", BLANK])
def test_append_entry_records_the_approver_it_judged(repo, tmp_path, pad):
    _hash_identity(repo)
    plain, pins = _handoff(repo)
    A.append_entry(repo, plain, "design-approval", "approve", LEAD, pins, author=DEV,
                   self_approval="forbid")
    padded, pins = _handoff(repo)
    A.append_entry(repo, padded, "design-approval", "approve", pad + LEAD + pad, pins,
                   author=DEV, self_approval="forbid")
    entry = A.read_chain(repo, padded)[-1]
    assert entry["approver"] == LEAD
    assert entry["hash"] == A.entry_hash(entry), "the approver is inside the hashed body"
    decided = [e for e in E.read(repo, padded) if e["event"] == E.GATE_DECIDED]
    assert [e["approver"] for e in decided] == [LEAD]
    done = _export(repo, tmp_path / "out")
    assert done.returncode == 0, done.stdout + done.stderr
    tokens = _tokens(tmp_path / "out")
    assert len(tokens) == 1 and next(iter(tokens)).startswith("person-"), tokens


# ── 2-3: record, as a library, admits and records by the same helper ─────────


@pytest.mark.parametrize("approver", ["   ", "\t", BLANK, 12345])
def test_record_refuses_a_blank_approver_as_a_library(repo, approver):
    forbid(repo)
    rid = _run_at(repo)
    before = _bytes(repo, rid)
    with pytest.raises(RuntimeError) as refused:
        A.record(repo, rid, "G3", "approve", approver, author=DEV)
    assert str(refused.value) == ("no approver: pass --approver, or set git config "
                                  "user.email in the target - an approval attributed "
                                  "to nobody is not one")
    assert _bytes(repo, rid) == before
    done = verify(repo, rid)
    assert done.returncode == 0 and "ledger: 0 entries" in done.stdout, done.stdout
    # The remedy, followed - a real approver - is accepted.
    A.record(repo, rid, "G3", "approve", LEAD, author=DEV)
    assert [e["approver"] for e in A.read_chain(repo, rid)] == [LEAD]


def test_record_records_a_padded_library_approver_stripped(repo):
    forbid(repo)
    rid = _run_at(repo)
    entry = A.record(repo, rid, "G3", "approve", " " + LEAD + " ", author=DEV)
    assert entry["approver"] == LEAD
    assert A.read_chain(repo, rid)[-1]["approver"] == LEAD
    decided = [e for e in E.read(repo, rid) if e["event"] == E.GATE_DECIDED]
    assert [e["approver"] for e in decided] == [LEAD]
    history = L.load(repo, rid)["history"]
    assert history[-1]["note"] == f"G3 approved by {LEAD}"
    done = verify(repo, rid)
    assert done.returncode == 0 and "not judged" not in done.stdout, done.stdout


# ── 4: back-compat (DOCUMENTS - passes before and after T712, by design) ─────


def test_BACK_COMPAT_an_existing_padded_entry_keeps_its_bytes_and_hash(repo):
    """Normalisation happens only on the way in: an entry already recorded padded is
    never rewritten, keeps its hash, and the chain still verifies intact with a new
    entry linked after it."""
    rid = _pre_t712_run(repo)
    A.append_entry(repo, rid, "design-approval", "approve", LEAD,
                   {"handoffs/design-1.md": A.read_chain(repo, rid)[0]["artifacts"][
                       "handoffs/design-1.md"]}, author=DEV, self_approval="forbid")
    lines = A.approvals_path(repo, rid).read_bytes().decode("utf-8").splitlines(True)
    assert lines[0] == PRE_T712_APPROVALS
    assert len(lines) == 2
    assert E.events_path(repo, rid).read_bytes().decode("utf-8").startswith(
        PRE_T712_EVENTS)
    done = verify(repo, rid)
    assert done.returncode == 0 and "ledger: 2 entries, intact" in done.stdout, done.stdout


# ── 5: the export pseudonymises the identity the judge reads ─────────────────


def test_an_existing_padded_approver_exports_as_the_same_person(repo, tmp_path):
    _hash_identity(repo)
    old = _pre_t712_run(repo)
    plain, pins = _handoff(repo)
    A.append_entry(repo, plain, "design-approval", "approve", LEAD, pins, author=DEV,
                   self_approval="forbid")
    before = _bytes(repo, old), _bytes(repo, plain)
    done = _export(repo, tmp_path / "out")
    assert done.returncode == 0, done.stdout + done.stderr
    tokens = _tokens(tmp_path / "out")
    assert len(tokens) == 1 and next(iter(tokens)).startswith("person-"), tokens
    assert (_bytes(repo, old), _bytes(repo, plain)) == before, \
        "an export never writes the ledger or the events"


# ── fix round 1 ──────────────────────────────────────────────────────────────

#: What the pre-T712 export wrote for the padded approver: the raw value, hashed.
OLD_TOKEN = M.pseudonym("  " + LEAD + "  ")
NEW_TOKEN = M.pseudonym(LEAD)


def _flat(text: str) -> str:
    return " ".join(text.split()).replace("`", "")


def test_a_derived_store_keeps_its_token_until_it_is_rebuilt_as_the_doors_say(repo):
    """F1: `audit.db` is keyed on the event hash (`INSERT OR IGNORE`), which T712 does
    not move, so a store that already holds a padded row keeps the pre-T712 token - a
    re-export, `--force` or not, never reaches it. The doors say so and name the remedy
    (delete the store, re-export); following it re-derives one person."""
    import sqlite3

    _hash_identity(repo)
    old = _pre_t712_run(repo)
    plain, pins = _handoff(repo)
    A.append_entry(repo, plain, "design-approval", "approve", LEAD, pins, author=DEV,
                   self_approval="forbid")
    db = repo / ".sdlc" / "metrics" / "audit.db"

    def export() -> set[str]:
        done = _spawn([str(METRICS), "--target", str(repo), "--export", "sqlite"])
        assert done.returncode == 0, done.stdout + done.stderr
        con = sqlite3.connect(str(db))
        try:
            return {row[0] for row in con.execute("SELECT approver FROM gates")}
        finally:
            con.close()

    assert export() == {NEW_TOKEN}
    # The store as the pre-T712 export left it: the padded run's row under the old token.
    con = sqlite3.connect(str(db))
    with con:
        con.execute("UPDATE gates SET approver = ? WHERE run_id = ?", (OLD_TOKEN, old))
    con.close()
    assert export() == {OLD_TOKEN, NEW_TOKEN}, "a re-export does not reach a stored row"
    # The remedy the doors name: delete the store and export again.
    db.unlink()
    assert export() == {NEW_TOKEN}
    # The doors that state the consequence name that remedy.
    remedy = ("keeps the token it was first written with; to re-derive it, delete the "
              "store and export again")
    readme = _flat((ROOT / "README.md").read_text(encoding="utf-8"))
    guide = _flat((ROOT / "docs" / "warehouse-loading.md").read_text(encoding="utf-8"))
    assert remedy in readme, "README"
    assert remedy in guide, "docs/warehouse-loading.md"
    helped = _spawn([str(METRICS), "--help"], COLUMNS="4000")
    helped = _flat(helped.stdout)
    assert "--force with --export ndjson|csv: replace" in helped, helped
    # Fix round 2, R2: what a re-export does to a store, listed - not "never rewritten".
    assert ("a sqlite re-export adds rows and fills a run's closure into its runs row, and "
            "never changes a row's exported values, an approver's token included") in helped
    assert "never rewritten" not in helped, helped


def test_the_re_export_refusal_names_a_cause_true_for_a_changed_export(repo, tmp_path):
    """F4: re-exporting a padded run over its pre-T712 export is refused - nothing grew,
    what the export writes for it changed - and the refusal must say a cause true here."""
    _hash_identity(repo)
    _pre_t712_run(repo)
    out = tmp_path / "out"
    assert _export(repo, out).returncode == 0
    (written,) = [f for f in out.rglob("*.ndjson") if f.parent.parent.name == "gate_decided"]
    # The file as the pre-T712 export wrote it: the old token.
    written.write_bytes(written.read_bytes().replace(NEW_TOKEN.encode(), OLD_TOKEN.encode()))
    refused = _export(repo, out)
    said = _flat(refused.stdout + refused.stderr)
    assert refused.returncode != 0, said
    assert "the run's exported rows changed since it was exported" in said, said
    assert "--force" in said, said
    forced = _spawn([str(METRICS), "--target", str(repo), "--export", "ndjson", "--out",
                     str(out), "--force"])
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert _tokens(out) == {NEW_TOKEN}
    guide = _flat((ROOT / "docs" / "warehouse-loading.md").read_text(encoding="utf-8"))
    assert "whose exported rows changed" in guide, "docs/warehouse-loading.md"
