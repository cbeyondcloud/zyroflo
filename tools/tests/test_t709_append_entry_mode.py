"""T709 - `append_entry` records the `self_approval` mode its caller enforced.

A caller running a pipeline of its own wrote approvals through `append_entry`, which
took an `author=` but no mode, and `extra` could not carry the mode (a core field). The
verifiers judge an entry only under the mode it records, so every such entry was "not
judged" for the life of the repository. Now `append_entry(..., self_approval=...)`
records `allow` or `forbid`, refusing at write - by the call `record` makes - a
self-approval or an unknown author under `forbid`.

`append_entry` is a library with no CLI, so each test writes through it in-process and
then SPAWNS `approve.py --verify` and the emitted `.sdlc/ledger_check.py` for the verdict
a person sees. Every test fails on the pre-T709 code except the one named
`test_BACK_COMPAT_*`, which documents that a caller passing no mode writes the old bytes.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.tests.test_t707_ledger_author import (  # noqa: E402,F401 - fixtures
    AT, BLANK, DEV, LEAD, PRE_T707, _handoff, _ledger_check, _restamp, forbid, repo,
    verify)


def _written(repo: Path, rid: str) -> tuple[bytes, bytes]:
    """What a refused call must leave untouched: the approvals file's and the events'
    BYTES (b"" when absent), so an append before a refusal on a chain that already
    holds entries is seen too."""
    return tuple(p.read_bytes() if p.exists() else b""
                 for p in (A.approvals_path(repo, rid), E.events_path(repo, rid)))


def _approval(repo: Path, author, mode: str | None, **kw) -> str:
    rid, pins = _handoff(repo)
    A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins, author=author,
                   self_approval=mode, **kw)
    return rid


# ── 1: forbid, two people - judged, not "not judged" ─────────────────────────


def test_forbid_with_a_different_author_is_judged_ok_at_both_doors(repo, tmp_path):
    rid = _approval(repo, DEV, "forbid")
    entry = A.read_chain(repo, rid)[-1]
    assert (entry["author"], entry["self_approval"]) == (DEV, "forbid")
    done = verify(repo, rid)
    assert done.returncode == 0 and "not judged" not in done.stdout, done.stdout
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0 and "not checked" not in done.stdout, done.stdout


# ── 2-3: forbid over a self-approval or an unknown author is refused at write ─


#: The library door's refusals (fix round 1, F1): they name the rule's true source -
#: the caller's `self_approval=` argument, not the policy file - and a remedy a
#: library caller can follow (`author=`). `record`'s CLI text is pinned in test_policy.
SELF_REFUSAL = (f"self_approval='forbid' was passed - {LEAD} wrote this change and may "
                f"not also decide it")
UNKNOWN_REFUSAL = ("self_approval='forbid' was passed, and there is no recorded author to "
                   "compare against - pass author= with the change's author")


def _allow_policy(repo: Path) -> None:
    """The repository's policy says `allow`: a refusal citing the policy would be false."""
    (repo / ".sdlc" / "policy.yml").write_bytes(b"self_approval: allow\n")


@pytest.mark.parametrize("verdict", ["approve", "reject"])
@pytest.mark.parametrize("author", [LEAD, LEAD.upper(), f"  {LEAD}\t"])
def test_forbid_refuses_a_self_approval_and_writes_nothing(repo, author, verdict):
    """Any verdict, a rejection included (owner, Q2 A: one rule at both doors, as
    `record` refuses it) - so a narrowing to approvals only goes red here. The refusal
    lands on a chain that already holds an entry, so a write before it would show."""
    _allow_policy(repo)
    rid, pins = _handoff(repo)
    A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins, author=DEV,
                   self_approval="forbid")
    before = _written(repo, rid)
    with pytest.raises(RuntimeError) as refused:
        A.append_entry(repo, rid, "design-approval", verdict, LEAD, pins,
                       author=author, self_approval="forbid")
    assert str(refused.value) == SELF_REFUSAL
    assert _written(repo, rid) == before
    # Mutation: a different person is accepted.
    A.append_entry(repo, rid, "design-approval", verdict, LEAD, pins, author=DEV,
                   self_approval="forbid")
    assert len(A.read_chain(repo, rid)) == 2


@pytest.mark.parametrize("author", [None, "", BLANK])
def test_forbid_refuses_an_unknown_author_and_writes_nothing(repo, author):
    _allow_policy(repo)
    rid, pins = _handoff(repo)
    before = _written(repo, rid)
    with pytest.raises(RuntimeError) as refused:
        A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins,
                       author=author, self_approval="forbid")
    assert str(refused.value) == UNKNOWN_REFUSAL
    assert _written(repo, rid) == before
    # The remedy, followed, is accepted.
    A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins, author=DEV,
                   self_approval="forbid")
    assert len(A.read_chain(repo, rid)) == 1


@pytest.mark.parametrize("approver", ["   ", "\t", BLANK])
def test_a_blank_approver_is_refused_and_writes_nothing(repo, approver):
    """F3: the write-time check is the judge's own helper (`known_identity`), so a
    whitespace approver cannot record a `forbid` claim nothing can judge."""
    rid, pins = _handoff(repo)
    before = _written(repo, rid)
    with pytest.raises(RuntimeError, match="no approver"):
        A.append_entry(repo, rid, "design-approval", "approve", approver, pins,
                       author=DEV, self_approval="forbid")
    assert _written(repo, rid) == before


def test_an_allow_entry_with_no_author_names_a_caller_that_passed_none():
    """F4: `allow` keeps the author optional, so an entry with a mode and no author is
    written today; judged under `forbid` by a caller of its own, its reason must not
    claim age alone."""
    entry = {"verdict": "approve", "approver": LEAD, "gate": "g", "self_approval": "allow"}
    verdict, why = A.separation_of_duties(entry, "forbid")
    assert verdict == "not-judged"
    assert why == ("the entry records no author (written before entries recorded one, "
                   "or by a caller that passed none)")


# ── 4: allow is recorded and judged not-applicable; stated weaker under forbid ─


def test_allow_is_not_applicable_and_stated_once_under_a_forbid_policy(repo):
    rid = _approval(repo, LEAD, "allow")
    done = verify(repo, rid)
    assert done.returncode == 0 and "not judged" not in done.stdout, done.stdout
    assert "[weaker than policy]" not in done.stdout, done.stdout
    forbid(repo)
    done = verify(repo, rid)
    assert done.returncode == 0, done.stdout
    assert done.stdout.count("[weaker than policy]") == 1, done.stdout


# ── 5-6: an unknown mode, and a mode smuggled through extra ──────────────────


@pytest.mark.parametrize("mode", ["maybe", "", True])
def test_an_unknown_mode_is_refused_naming_it(repo, mode):
    rid, pins = _handoff(repo)
    before = _written(repo, rid)
    with pytest.raises(ValueError, match="unknown self_approval mode " + repr(mode)):
        A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins, author=DEV,
                       self_approval=mode)
    assert _written(repo, rid) == before


def test_a_mode_through_extra_is_refused_naming_the_keyword(repo):
    rid, pins = _handoff(repo)
    with pytest.raises(ValueError, match="pass it as the self_approval= keyword"):
        A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins, author=DEV,
                       extra={"self_approval": "forbid"})
    assert not A.approvals_path(repo, rid).exists()


# ── 7: the signature binds the mode ──────────────────────────────────────────


def test_the_signed_payload_carries_the_mode_and_an_edit_breaks_it(repo, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = tmp_path / "signing.pem"
    key.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    rid = _approval(repo, DEV, "forbid", signer=A.signer_from(key))
    entry = A.read_chain(repo, rid)[-1]
    payload = json.loads(base64.b64decode(entry["signature"]["dsse"]["payload"]))
    assert payload["self_approval"] == "forbid"
    assert verify(repo, rid).returncode == 0

    entry["self_approval"] = "allow"
    entry["hash"] = A.entry_hash(entry)
    A.approvals_path(repo, rid).write_bytes(
        (json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    assert A.verify_chain(repo, rid) == []
    done = verify(repo, rid)
    assert done.returncode == 1 and "signature is over different bytes" in done.stdout, \
        done.stdout


# ── 8: the two doors agree on every verdict an append_entry entry reaches ────


@pytest.mark.parametrize("case", ["ok", "self-approved", "not-applicable"])
def test_verify_and_the_emitted_check_agree(repo, tmp_path, case):
    if case == "ok":
        rid = _approval(repo, DEV, "forbid")
    elif case == "self-approved":
        # The writer now refuses this, so it exists only as an edit leaves it.
        rid = _approval(repo, DEV, "forbid")
        _restamp(repo, rid, author=LEAD)
    else:
        rid = _approval(repo, LEAD, "allow")
    done, check = verify(repo, rid), _ledger_check(repo, tmp_path)
    if case == "self-approved":
        assert done.returncode == 1 and "self-approval" in done.stdout, done.stdout
        assert check.returncode == 1 and "self-approval" in check.stdout, check.stdout
    else:
        assert done.returncode == 0 and "not judged" not in done.stdout, done.stdout
        assert check.returncode == 0 and "not checked" not in check.stdout, check.stdout


# ── 9: the not-judged reason no longer claims its cause is age ───────────────


def test_a_no_mode_entry_names_a_caller_that_passed_none(repo):
    rid, pins = _handoff(repo)
    A.append_entry(repo, rid, "design-approval", "approve", LEAD, pins, author=DEV)
    done = verify(repo, rid)
    assert done.returncode == 0, done.stdout
    assert "or by a caller that passed none" in done.stdout, done.stdout


# ── 10: back-compat (DOCUMENTS - passes before and after T709, by design) ────


def test_BACK_COMPAT_no_mode_keyword_writes_the_pre_t707_bytes(repo, monkeypatch):
    monkeypatch.setattr(A, "_now", lambda: AT)
    rid, pins = _handoff(repo, "r-t707-pre")
    for verdict in ("reject", "approve"):
        A.append_entry(repo, rid, "design-approval", verdict, LEAD, pins, notes="pre-T707",
                       **({} if "self_approval" not in A.append_entry.__kwdefaults__
                          else {"self_approval": None}))
    assert A.approvals_path(repo, rid).read_bytes() == PRE_T707.encode("utf-8")
