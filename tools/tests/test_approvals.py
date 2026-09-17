"""Item 32 — the canonical approval ledger, and what is waiting for a human.

The tests are mostly about refusing and about detecting: an approval that
names nobody, a yes about work that does not exist yet, a line edited after
the fact, an artifact edited after its yes. Each is a way a gate could look
passed without being passed, which is the only failure mode that matters in a
file whose whole job is to be the record a merge is defended with.
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
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import stage as PHASES  # noqa: E402
from tools.sdlc import status as S  # noqa: E402
from tools.tests import _runs as R  # noqa: E402
# Repository membership for the sweep below, reused rather than re-implemented:
# `test_workflows` imports from this same module for the same reason, and the
# helper's own docstring names the defect a walk has here. See item 115.
from tools.tests.test_dependencies import _sources  # noqa: E402

WHO = "dev@example.invalid"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True)
    return r


def _run_at(repo: Path, phase: str = "P4") -> str:
    """A run parked at a gate, with one recorded artifact to pin — and having
    delivered what the phase owes, because item 36 made the gate stand on it."""
    st = L.create(repo, "requirement", "a change worth gating")
    (L.run_dir(repo, st["run_id"]) / "design.md").write_text("the plan",
                                                             encoding="utf-8")
    L.record_artifact(repo, st["run_id"], "design.md")
    R.park(repo, st["run_id"], phase)
    return st["run_id"]


# ── the entry, and the chain ─────────────────────────────────────────────────


def test_the_hash_covers_everything_except_itself():
    entry = {"gate": "G3", "verdict": "approve", "prev": ""}
    first = A.entry_hash(entry)
    assert A.entry_hash({**entry, "hash": "anything at all"}) == first
    assert A.entry_hash({**entry, "notes": "changed"}) != first


def test_the_first_entry_is_a_genesis_and_pins_the_artifacts(repo):
    rid = _run_at(repo)
    entry = A.record(repo, rid, "G3", "approve", WHO, notes="looks right")
    assert entry["prev"] == "" and entry["hash"].startswith("sha256:")
    assert entry["artifacts"] == L.load(repo, rid)["artifacts"]
    assert entry["gate_profile"] == "solo" and entry["signing"] == "none"
    assert A.verify_chain(repo, rid) == []


def test_each_entry_links_to_the_one_before(repo):
    rid = _run_at(repo)
    first = A.record(repo, rid, "G3", "approve", WHO)
    R.park(repo, rid, "P7")
    second = A.record(repo, rid, "G4", "approve", WHO)
    assert second["prev"] == first["hash"]
    assert A.verify_chain(repo, rid) == []


def test_an_edited_entry_stops_matching_its_own_hash(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    path = A.approvals_path(repo, rid)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["notes"] = "silently widened"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")
    assert "does not match its contents" in " ".join(A.verify_chain(repo, rid))


def test_re_hashing_an_edited_entry_breaks_the_next_ones_link(repo):
    """The knowledgeable tamper: fix up the hash you just invalidated."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    R.park(repo, rid, "P7")
    A.record(repo, rid, "G4", "approve", WHO)
    path = A.approvals_path(repo, rid)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["notes"] = "widened, and re-hashed to cover it"
    rows[0]["hash"] = A.entry_hash(rows[0])
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8", newline="\n")
    problems = " ".join(A.verify_chain(repo, rid))
    assert "entry 2" in problems and "edited, inserted or removed" in problems


def test_an_unreadable_line_stops_the_walk_and_says_so(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    with A.approvals_path(repo, rid).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json\n")
    assert "not readable JSON" in " ".join(A.verify_chain(repo, rid))
    with pytest.raises(RuntimeError, match="repair it"):
        A.record(repo, rid, "G4", "approve", WHO, force=True)


def test_the_ledger_is_written_lf(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert b"\r\n" not in A.approvals_path(repo, rid).read_bytes()


# ── an approval is of specific bytes ─────────────────────────────────────────


@pytest.mark.parametrize("wreck, expect", [
    (lambda p: p.write_text("edited after the yes", encoding="utf-8"), "changed since"),
    (lambda p: p.unlink(), "now missing"),
])
def test_an_artifact_touched_after_approval_voids_the_gate(repo, wreck, expect):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert A.gate_state(repo, rid, "G3") == (True, "")
    wreck(L.run_dir(repo, rid) / "design.md")
    ok, why = A.gate_state(repo, rid, "G3")
    assert not ok and expect in why


def test_gate_state_answers_the_runners_question(repo):
    rid = _run_at(repo)
    ok, why = A.gate_state(repo, rid, "G3")
    assert not ok and "no entry" in why
    A.record(repo, rid, "G3", "reject", WHO, notes="not yet")
    ok, why = A.gate_state(repo, rid, "G3")
    assert not ok and "rejected by" in why


# ── refusing ─────────────────────────────────────────────────────────────────


def test_an_approval_attributed_to_nobody_is_refused(repo):
    rid = _run_at(repo)
    with pytest.raises(RuntimeError, match="attributed to nobody"):
        A.record(repo, rid, "G3", "approve", "")


def test_unknown_gates_and_verdicts_are_refused(repo):
    rid = _run_at(repo)
    with pytest.raises(ValueError, match="unknown gate"):
        A.record(repo, rid, "G9", "approve", WHO)
    with pytest.raises(ValueError, match="unknown verdict"):
        A.record(repo, rid, "G3", "maybe", WHO)


def test_a_yes_about_work_that_does_not_exist_is_refused(repo):
    """G4 stands at P7; a run at P4 has not produced what G4 approves."""
    rid = _run_at(repo, phase="P4")
    with pytest.raises(RuntimeError, match="does not exist yet"):
        A.record(repo, rid, "G4", "approve", WHO)
    entry = A.record(repo, rid, "G4", "approve", WHO, force=True)
    assert entry["forced"] is True, "the override is recorded, not just allowed"


def test_an_abandoned_run_takes_no_further_approvals(repo):
    """`rejected` is the operator ABANDONING a run, and that is terminal.

    It used to be what a rejected GATE did too, until item 42 found that made
    the plan's own concurrent-verdict rule unimplementable - a halt "until a
    fresh approval is recorded" cannot happen on a run nothing may be recorded
    against. The two decisions are now distinct; this one still ends the run.
    """
    rid = _run_at(repo)
    L.advance(repo, rid, status="rejected", note="not going to happen")
    with pytest.raises(RuntimeError, match="terminal run takes no further"):
        A.record(repo, rid, "G3", "approve", WHO)


def test_a_rejected_gate_halts_the_run_rather_than_ending_it(repo):
    """Rejection at a gate is the ORDINARY case - the SOPs say open findings
    loop back to design. A run that a single `no` killed for good could not do
    that, and could never record the fresh approval the rule requires."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "reject", WHO, notes="not yet")
    state = L.load(repo, rid)
    assert state["status"] == "halted" and state["status"] not in L.TERMINAL
    assert "halts until the work changes" in state["history"][-1]["note"]


def test_only_a_ledger_entry_unblocks_the_run(repo):
    rid = _run_at(repo)
    assert L.load(repo, rid)["status"] == "awaiting_approval"
    A.record(repo, rid, "G3", "approve", WHO)
    st = L.load(repo, rid)
    assert st["status"] == "running"
    assert "G3 approved by" in st["history"][-1]["note"]


# ── signing ──────────────────────────────────────────────────────────────────


def signing_key(where: Path) -> Path:
    """An unencrypted PEM ed25519 key, generated per test.

    Generated, never committed: CLAUDE.md's first ground rule is that a secret
    never lives in any file but `.env`, and a fixture key checked into the tree
    would be a private key in git history whatever its comment said.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    path = where / "signing.pem"
    path.write_bytes(pem)
    return path


def regulated(repo: Path) -> None:
    (repo / ".sdlc" / "profile.yml").write_text(
        "gate_profile: regulated\nsigning: dsse\n", encoding="utf-8")


def test_dsse_is_refused_when_there_is_no_key(repo, monkeypatch):
    """Never a silent downgrade to unsigned: an entry that looks attributed and
    is not is worse than no entry.

    **This test used to assert the refusal said "not implemented yet".** That
    was the honest answer while envelope writing did not exist, and item 108
    made it false - `gate_profile: regulated` could not approve ANY gate, so one
    of the three shipped regimes was dead config. The property it was really
    pinning survives unchanged: a `dsse` run with no key writes nothing."""
    regulated(repo)
    monkeypatch.delenv(A.SIGNING_KEY_ENV, raising=False)
    rid = _run_at(repo)
    with pytest.raises(RuntimeError) as exc:
        A.record(repo, rid, "G3", "approve", WHO)
    said = str(exc.value)
    assert A.SIGNING_KEY_ENV in said and "no key to sign with" in said
    assert "not implemented" not in said, "the feature exists now"
    assert A.approvals_path(repo, rid).exists() is False, "nothing was written"


def test_a_regulated_run_signs_and_the_signature_verifies(repo, monkeypatch, tmp_path):
    """**The item: `regulated` can take a gate.** Before this, `dsse_blockers`
    returned a blocker unconditionally, so the regime refused every gate."""
    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(signing_key(tmp_path)))
    assert A.dsse_blockers(repo) == []
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    entry = A.read_chain(repo, rid)[-1]
    assert entry["signing"] == "dsse"
    assert entry["signature"]["dsse"]["payloadType"] == A.PAYLOAD_TYPE
    assert A.verify_signatures(repo, rid) == []
    assert A.verify_chain(repo, rid) == [], "signing must not break the chain"


def test_the_chain_and_the_signature_agree_by_construction(repo, monkeypatch, tmp_path):
    """The hash excludes `signature` and the envelope attests the same
    canonical body, so neither can invalidate the other. An unsigned ledger
    written before signing existed still verifies - the exclusion adds no key
    to an entry that has none."""
    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(signing_key(tmp_path)))
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    entry = A.read_chain(repo, rid)[-1]
    assert A.entry_hash(entry) == entry["hash"]
    without = {k: v for k, v in entry.items() if k != "signature"}
    assert A.entry_hash(without) == entry["hash"], \
        "the hash must not depend on the signature being present"


def test_an_edited_signed_entry_fails_verification(repo, monkeypatch, tmp_path):
    """**The gap signing closes.** Item 32 recorded that an unsigned chain
    detects an edit but not a wholesale rewrite, because a rewriter re-chains
    cleanly. Here the rewriter re-chains AND the signature still fails, because
    it cannot be recomputed without the key."""
    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(signing_key(tmp_path)))
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)

    path = A.approvals_path(repo, rid)
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    entry["verdict"] = "approve"
    entry["approver"] = "somebody-else@example.invalid"
    entry["hash"] = A.entry_hash(entry)          # re-chained, as a rewriter would
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    assert A.verify_chain(repo, rid) == [], "the rewrite re-chains cleanly"
    problems = A.verify_signatures(repo, rid)
    assert problems and "different bytes" in problems[0]


def test_a_signature_lifted_from_another_entry_is_refused(repo, monkeypatch, tmp_path):
    """A valid envelope is not the same as an envelope about THIS entry. Without
    the payload comparison, a block copied from a real approval would verify
    perfectly against its own key."""
    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(signing_key(tmp_path)))
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    stolen = A.read_chain(repo, rid)[-1]["signature"]

    other = _run_at(repo)
    A.record(repo, other, "G3", "approve", WHO)
    path = A.approvals_path(repo, other)
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    entry["signature"] = stolen
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    problems = A.verify_signatures(repo, other)
    assert problems and "different bytes" in problems[0]


def test_a_signature_nobody_can_check_is_not_a_pass(repo, monkeypatch, tmp_path):
    """A verifier without the library must SAY it checked nothing. Reporting a
    signed ledger as fine having verified nothing is *nothing ran* spelled the
    same way as *nothing failed*."""
    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(signing_key(tmp_path)))
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)

    import builtins
    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name.startswith("securesystemslib"):
            raise ImportError("pretend it is absent")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    problems = A.verify_signatures(repo, rid)
    assert problems and "not installed" in problems[0]
    assert "do not read this as a pass" in problems[0]


def test_an_unsigned_ledger_needs_no_library_to_report_clean(repo):
    """The other half: a repository that never signs must not be told it is
    missing something. Absent is not a problem; unverifiable is."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert A.verify_signatures(repo, rid) == []


def _signed(repo: Path, monkeypatch, tmp_path) -> tuple[str, Path, dict]:
    """One signed approval, and its entry read back off disk."""
    import json as _json

    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(signing_key(tmp_path)))
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    path = A.approvals_path(repo, rid)
    return rid, path, _json.loads(path.read_text(encoding="utf-8").strip())


def test_a_corrupted_signature_fails_the_cryptographic_check(repo, monkeypatch,
                                                             tmp_path):
    """**The check that actually does the work, and nothing was reaching it.**

    The tamper test above edits the entry, so the payload comparison fires
    first and `envelope.verify` never decides anything - a mutation pass proved
    it by removing the verify call and watching the suite stay green. Here the
    payload, the key and the payload type all match and ONLY the signature
    bytes are wrong, so the cryptographic check is the sole discriminator."""
    rid, path, entry = _signed(repo, monkeypatch, tmp_path)
    sigs = entry["signature"]["dsse"]["signatures"]
    good = sigs[0]["sig"]
    sigs[0]["sig"] = ("0" * len(good)) if good[0] != "0" else ("1" * len(good))
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    problems = A.verify_signatures(repo, rid)
    assert problems and "does not verify" in problems[0], problems


def test_an_envelope_of_the_wrong_type_is_refused(repo, monkeypatch, tmp_path):
    """A verifier that ignores `payloadType` would accept an envelope built for
    something else entirely, signed by a key it happens to trust."""
    rid, path, entry = _signed(repo, monkeypatch, tmp_path)
    entry["signature"]["dsse"]["payloadType"] = "application/vnd.something-else"
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    problems = A.verify_signatures(repo, rid)
    assert problems and "declares" in problems[0], problems


@pytest.mark.parametrize("block", [
    {"dsse": "not a mapping", "public_key": {}, "keyid": "x"},
    {"dsse": {"payload": "", "payloadType": "", "signatures": []}},
    {"keyid": "x"},
])
def test_a_signature_block_nobody_can_parse_is_reported_not_skipped(
        repo, monkeypatch, tmp_path, block):
    """**Skipping it would be the silent drop this project bans.** An entry
    claiming a signature the verifier cannot even read must not travel as
    though it carried none - `continue` there was a mutation that survived."""
    rid, path, entry = _signed(repo, monkeypatch, tmp_path)
    entry["signature"] = block
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    problems = A.verify_signatures(repo, rid)
    assert problems, "a signature nobody can read reported nothing"
    assert "cannot be read" in problems[0], problems


def test_an_unreadable_key_refuses_by_name(repo, monkeypatch, tmp_path):
    """An encrypted key is the likely case, and the harness has nowhere to put
    a passphrase that would not itself become a secret in a file."""
    bad = tmp_path / "encrypted.pem"
    # The PEM banner is CONSTRUCTED, never a literal. gitleaks' `private-key`
    # rule matches the banner itself, so the literal form tripped the secret
    # gate on a fixture whose payload is the words "not a key" - and CI's
    # `detect` walks git HISTORY, so it stayed found after the file changed.
    band = "-" * 5
    bad.write_text(f"{band}BEGIN PRIVATE KEY{band}" + chr(10) + "not a key"
                   + chr(10) + f"{band}END PRIVATE KEY{band}" + chr(10),
                   encoding="utf-8")
    regulated(repo)
    monkeypatch.setenv(A.SIGNING_KEY_ENV, str(bad))
    rid = _run_at(repo)
    with pytest.raises(RuntimeError, match="unencrypted PEM private key"):
        A.record(repo, rid, "G3", "approve", WHO)
    assert A.approvals_path(repo, rid).exists() is False


def test_an_unknown_signing_mode_is_refused(repo):
    (repo / ".sdlc" / "profile.yml").write_text("signing: homemade\n",
                                                encoding="utf-8")
    rid = _run_at(repo)
    with pytest.raises(RuntimeError, match="unknown signing mode"):
        A.record(repo, rid, "G3", "approve", WHO)


def test_a_profile_without_a_gate_profile_records_unset(tmp_path):
    r = tmp_path / "bare"
    (r / ".sdlc").mkdir(parents=True)
    rid = _run_at(r)
    entry = A.record(r, rid, "G3", "approve", WHO)
    assert entry["gate_profile"] == "unset" and entry["signing"] == "none"


def test_the_approver_falls_back_to_the_repositorys_git_identity(repo):
    subprocess.run(["git", "-C", str(repo), "config", "user.email", WHO],
                   check=True, capture_output=True)
    assert A.who(repo, None) == WHO
    assert A.who(repo, "  someone-else  ") == "someone-else"


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_records_and_then_verifies(repo, capsys):
    rid = _run_at(repo)
    assert A.main(["--target", str(repo), "--run", rid, "--gate", "G3",
                   "--verdict", "approve", "--approver", WHO]) == 0
    assert "G3 approved" in capsys.readouterr().out
    assert A.main(["--target", str(repo), "--run", rid, "--verify"]) == 0
    assert "intact" in capsys.readouterr().out
    (L.run_dir(repo, rid) / "design.md").write_text("moved on", encoding="utf-8")
    assert A.main(["--target", str(repo), "--run", rid, "--verify"]) == 1


def test_cli_refuses_without_a_gate_or_a_target(repo, capsys):
    rid = _run_at(repo)
    assert A.main(["--target", str(repo), "--run", rid]) == 2
    assert "--gate and --verdict" in capsys.readouterr().out
    assert A.main(["--target", str(repo / "nowhere"), "--run", rid,
                   "--verify"]) == 2


# ── what is waiting for a human ──────────────────────────────────────────────


def test_status_names_the_gate_a_run_waits_at(repo, capsys):
    rid = _run_at(repo, phase="P7")
    assert S.waiting_on(L.load(repo, rid)) == "G4"
    assert S.main(["--target", str(repo), "--awaiting"]) == 0
    out = capsys.readouterr().out
    assert rid in out and "waiting G4" in out


def test_status_hides_runs_nobody_is_waiting_on(repo, capsys):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert S.main(["--target", str(repo), "--awaiting"]) == 0
    assert "nothing is waiting" in capsys.readouterr().out


def test_status_verify_is_the_signal_ci_will_read(repo, capsys):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert S.main(["--target", str(repo), "--verify"]) == 0
    (L.run_dir(repo, rid) / "design.md").write_text("edited after the yes",
                                                    encoding="utf-8")
    assert S.main(["--target", str(repo), "--verify"]) == 1
    assert "changed since" in capsys.readouterr().out


def test_status_shows_one_run_in_full(repo, capsys):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO, notes="diff read line by line")
    assert S.main(["--target", str(repo), "--run", rid]) == 0
    out = capsys.readouterr().out
    assert "G3 approved by" in out and "diff read line by line" in out
    assert S.main(["--target", str(repo), "--run", "ghost"]) == 2


def test_status_json_carries_the_survey(repo, capsys):
    rid = _run_at(repo)
    assert S.main(["--target", str(repo), "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["run_id"] == rid and row["waiting"] == "G3" and row["entries"] == 0


def test_status_reads_and_never_writes(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    before = {p: p.read_bytes() for p in L.run_dir(repo, rid).iterdir()
              if p.is_file()}
    S.main(["--target", str(repo)])
    S.main(["--target", str(repo), "--run", rid, "--verify"])
    assert {p: p.read_bytes() for p in L.run_dir(repo, rid).iterdir()
            if p.is_file()} == before


# ── the reader shows the ledger back without dying on it (item 139) ──────────


def test_status_detail_renders_a_missing_key_as_could_not_read(repo):
    """**Item 139, as a process (item 96).** A committed ledger is hand-editable,
    so a line can be valid JSON and a dict yet miss a key. status.py indexed
    `e['gate']` directly and printed a TRACEBACK - in the reader whose whole job
    is to show the ledger back - right after `verify_chain` had correctly named
    the break above it. Red at the pre-fix SHA: the process crashes with
    KeyError. Spawned, because an in-process call renders a crash and a refusal
    the same way."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    with A.approvals_path(repo, rid).open("a", encoding="utf-8",
                                          newline="\n") as fh:
        fh.write(json.dumps({"verdict": "approve", "approver": "hand@edit"})
                 + "\n")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "status.py"),
         "--target", str(repo), "--run", rid],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr and "KeyError" not in proc.stderr
    assert "(could not read)" in proc.stdout            # the missing gate / at
    assert "by hand@edit" in proc.stdout                # the readable fields stay


def test_status_overview_renders_an_unreadable_state_row(repo):
    """**Item 139 (T131's other half), a regression lock.** A run whose
    state.json is unreadable is a ROW, not a listing that aborts: `list_runs`
    returns it (T131) and overview must render it rather than crash or drop it.
    Green by design - it holds the reader to surviving what T131 taught the
    lister to emit - and spawned because the failure mode was a traceback."""
    rid = _run_at(repo)
    (L.run_dir(repo, rid) / L.STATE_NAME).write_text("{not json",
                                                     encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "status.py"),
         "--target", str(repo), "--verify"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "Traceback" not in proc.stderr and "KeyError" not in proc.stderr
    assert proc.returncode == 1                          # unreadable state is a problem
    assert rid in proc.stdout                            # the row is not omitted
    assert "not readable JSON" in proc.stdout            # and its reason is stated


# ── a gate must not be voided by a phase that runs after it ──────────────────
#
# Found on the first run to take the lifecycle's own P6-to-P4 loop, by asking
# the merge gate directly: nine problems, eight of them phases moving forward
# exactly as designed. Two mechanisms, and both had to go.


def _artifact(repo: Path, run_id: str, rel: str, body: str) -> None:
    (L.run_dir(repo, run_id) / rel).write_text(body, encoding="utf-8")
    L.record_artifact(repo, run_id, rel)


def test_a_gate_pins_its_own_work_and_not_a_later_phases(repo):
    """G3 is a verdict about an implementation. A test report written after it
    is not G3's to approve, and pinning one made the run's own progress void
    the gate that had authorised it."""
    rid = _run_at(repo)
    for rel in ("50-tests.md", "50-sca.json", "60-review.md", "prepass.json"):
        _artifact(repo, rid, rel, "written by a later phase")

    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")
    pinned = set(A.read_chain(repo, rid)[-1]["artifacts"])
    assert "design.md" in pinned, "the gate must still pin the work it is about"
    assert not (pinned & {"50-tests.md", "50-sca.json", "60-review.md",
                          "prepass.json"}), sorted(pinned)


def test_rerunning_a_later_phase_does_not_void_the_gate_that_authorised_it(repo):
    """The composition, which is the actual defect: approve, then let P5 and P6
    run again as the loop requires, and ask whether the gate still stands."""
    rid = _run_at(repo)
    _artifact(repo, rid, "50-tests.md", "round one")
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")

    _artifact(repo, rid, "50-tests.md", "round two, re-measured")
    entry = A.read_chain(repo, rid)[-1]
    assert A.verify_artifacts_for(repo, rid, entry) == [], \
        "P5 re-running is the mandated next step and must not void G3"
    assert A.gate_state(repo, rid, "G3")[0] is True


def test_a_superseded_entry_is_not_checked_against_todays_tree(repo):
    """Round one approved bytes round two replaced. Reporting that is true and
    about nothing - and it kept a correct change out of a merge."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "round one")
    _artifact(repo, rid, "design.md", "the plan, revised after review")
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "round two")

    live = A.live_entries(repo, rid)
    assert [e["notes"] for e in live] == ["round two"]
    assert A.verify_artifacts_for(repo, rid, live[0]) == []
    assert len(A.read_chain(repo, rid)) == 2, \
        "superseded entries stay in the chain; only the VERDICT is superseded"
    assert A.verify_chain(repo, rid) == [], "and the chain still covers both"


def test_the_real_edit_is_still_caught(repo):
    """The control that matters. Loosening this to fix the loop would have
    given away the property item 32 exists for."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")
    _artifact(repo, rid, "design.md", "edited after the yes")
    entry = A.live_entries(repo, rid)[-1]
    assert any("changed since G3" in p
               for p in A.verify_artifacts_for(repo, rid, entry))


def test_an_artifact_no_phase_declares_is_still_pinned(repo):
    """The rule is stated as an EXCLUSION on purpose. An include-list would
    quietly stop pinning anything the contract does not name, and an adopter
    recording their own evidence would find no gate covering it."""
    rid = _run_at(repo)
    _artifact(repo, rid, "notes-from-the-vendor.md", "not in any phase")
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")
    assert "notes-from-the-vendor.md" in A.read_chain(repo, rid)[-1]["artifacts"]


# ── a rejection's pins are not an approval's (item 138) ──────────────────────
#
# `verify_artifacts_for` answers "what changed since this entry APPROVED it".
# A rejection approved nothing - its pins mean "these bytes were REFUSED", which
# is stage.unanswered's subject - so reporting a rejection's moved pins as
# "changed since G3 approved it" is a sentence about an approval that never
# happened. It fired on the ordinary reject->re-scope loop and made both
# `approve --verify` and `status --verify` exit 1 over a run with nothing wrong.


def test_a_rejections_moved_pins_are_not_reported_as_an_approval(repo):
    """The defect, at the function that decides it. Red at the pre-fix SHA:
    the reject's pin, checked against the re-scoped tree, read as drift."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "reject", WHO, "not yet")
    _artifact(repo, rid, "design.md", "revised after the no")   # the re-scope loop
    reject = A.live_entries(repo, rid)[-1]
    assert reject["verdict"] == "reject"
    assert A.verify_artifacts_for(repo, rid, reject) == [], \
        "a rejection approved nothing, so nothing 'changed since it approved'"


def test_an_approvals_moved_pins_are_still_caught(repo):
    """The valid-but-wrong control (rule 28): the SAME moved bytes under an
    APPROVE verdict must still be reported. The fix keys on the verdict, not on
    the drift being absent, so this proves it did not just silence the check."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO, "yes")
    _artifact(repo, rid, "design.md", "revised after the yes")
    approved = A.live_entries(repo, rid)[-1]
    assert approved["verdict"] == "approve"
    assert any("changed since G3" in p
               for p in A.verify_artifacts_for(repo, rid, approved))


def test_verify_clis_pass_a_reject_then_rescope_loop(repo):
    """**Item 138 as a process (item 96).** A CLI's contract is its exit code,
    and an in-process call cannot tell a refusal from a traceback - so both
    `--verify` doors are spawned. Red at the pre-fix SHA: each exits 1 over a
    reject followed by the re-scope the lifecycle mandates."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "reject", WHO, "not yet")
    _artifact(repo, rid, "design.md", "revised after the no")  # edit AND re-record

    ap = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "approve.py"),
         "--target", str(repo), "--run", rid, "--verify"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert ap.returncode == 0, ap.stdout + ap.stderr
    assert "intact" in ap.stdout and "changed since" not in ap.stdout

    st = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "status.py"),
         "--target", str(repo), "--run", rid, "--verify"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert st.returncode == 0, st.stdout + st.stderr
    assert "changed since" not in st.stdout


def test_evidence_is_attribution_and_carries_no_obligation():
    """`conditional` means owed when a trigger fires and must have a judge -
    a test enforces that, and enforced it against the first cut of this field,
    which abused `conditional` for attribution."""
    from tools.sdlc import stage as ST

    assert {"prepass.json", "50-sca.json"} <= {
        name for s in ST.STAGES for name in s.evidence}
    judged = {s.phase for s in ST.STAGES if s.conditional}
    assert judged == set(ST.CONDITIONAL_JUDGE), \
        "evidence must not have leaked into conditional"


# ── item 135: one past tense, and it is not built by suffix ───────────────────
#
# **Found by the FIRST GATE EVER REJECTED.** Across three prior real runs the
# ledgers hold 19 approvals and ZERO rejections, so no operator had ever read a
# sentence about a rejection. Two sites built an English past tense by SUFFIX
# and each was right only for the verdict its author happened to be testing.
#
# **THE FILING SAID TWO SITES AND A SWEEP FOUND THREE**, which is this item's own
# subject arriving while it was being fixed. And the sweep corrected the filing's
# severity too:
#
#   * `approve.py` printing `{verdict}d` on the confirmation - *approved* right,
#     ***rejectd*** wrong. LIVE: it prints on every rejection.
#   * `status.py` printing `{verdict}d` for EVERY entry in the chain - LIVE, and
#     the worst of the three, because that module's whole job is reading the
#     ledger back to a person. It was not in the filing at all.
#   * `approve.py` printing `{verdict}ed` in `gate_state`'s refusal. The filing
#     called this "the worse one". MEASURED: that branch is reached only when NO
#     entry carries `approve`, so with today's two-word vocabulary the only
#     verdict that can arrive is `reject` and it renders correctly. It is a
#     latent hazard - a third verdict, or a hand-edited ledger - not a live
#     defect, and saying so is worth more than repeating the report.


def _rejected(repo: Path) -> str:
    run_id = _run_at(repo, "P4")
    A.record(repo, run_id, "G3", "reject", "someone@example.test",
             notes="not yet", force=True)
    return run_id


def test_the_confirmation_says_rejected_and_not_rejectd(repo, capsys):
    """**The live defect, through the CLI a person actually runs.**"""
    run_id = _run_at(repo, "P4")
    assert A.main(["--target", str(repo), "--run", run_id, "--gate", "G3",
                   "--verdict", "reject", "--approver", "someone@example.test",
                   "--notes", "not yet", "--force"]) == 0
    out = capsys.readouterr().out
    assert "G3 rejected by" in out
    assert "rejectd" not in out


def test_the_ledger_read_back_says_rejected_for_every_entry(repo, capsys):
    """**The third site, and the one the filing missed.** `status.py` prints the
    WHOLE chain, so a run's entire history read `G3 rejectd by ...` in the module
    whose only job is showing that history to a person."""
    run_id = _rejected(repo)
    S.main(["--target", str(repo), "--run", run_id])
    out = capsys.readouterr().out
    assert "G3 rejected by" in out
    assert "rejectd" not in out


def test_every_verdict_the_writer_accepts_has_a_past_tense(repo):
    """**Derived, so a third verdict fails HERE rather than in front of an
    operator.** A roster of two would go stale the moment somebody adds one, and
    the suffix rule this replaces was exactly a guess that generalised wrongly.
    """
    assert set(A.PAST_TENSE) == set(A.VERDICTS), (
        f"{sorted(set(A.VERDICTS) - set(A.PAST_TENSE))} can be recorded and "
        f"cannot be said")
    for verdict in A.VERDICTS:
        assert A.said(verdict) != verdict, "a past tense that is the verb"


def test_a_verdict_this_harness_does_not_know_is_quoted_as_data(repo):
    """`gate_state` walks a ledger this process did not write — the file is
    committed and hand-editable, which is the whole reason for the hash chain —
    so an unrecognised word must not be dressed in a sentence that reads like
    the harness understood it. Item 117's argument, one module along."""
    said = A.said("withdraw")
    assert "withdraw" in said
    assert "'withdraw'" in said or '"withdraw"' in said, (
        "an unknown verdict is repeated as prose, so a hand-edited ledger gets "
        "to write the harness's own sentences")


def test_a_gate_with_only_rejections_still_reads_correctly(repo):
    """The third site's branch, exercised. It is correct today and the filing
    said otherwise; this pins the reading measurement produced."""
    run_id = _rejected(repo)
    ok, why = A.gate_state(repo, run_id, "G3")
    assert ok is False
    assert "G3 was rejected by someone@example.test" == why


def test_no_shipped_module_builds_a_past_tense_by_suffix(repo):
    """**The mechanism that found the third site, kept.** A sweep is what turned
    a two-site report into a three-site fix, and the next site is the one this
    is for — this project's dominant shape is a rule hardened where somebody was
    looking, leaving its neighbours.

    **It had no positive control on its first cut and a mutation said so**:
    emptying the collector left every assertion green, because a sweep that
    finds nothing and a sweep that looks at nothing produce the same empty
    list. Item 115's rule — a guard nobody can watch fail is a guard nobody has
    checked.

    **And the control it grew was then neutralised by the walk underneath it.**
    Membership was `root.rglob("*.py")` behind the hand-written excludes
    `{tests, .venv}`, which cannot name what a filesystem walk actually finds:
    **gitignored is invisible to git, not to a walk.** `worktree.py` checks
    other runs out INSIDE the repository, so measured from the main tree with
    one sibling live this gathered 310 files of which 206 were that sibling's
    copies — and `len(corpus) > 20` was satisfied by the 206 foreign files
    ALONE. The one assertion meant to prove the sweep saw something real was
    green on garbage, and only a stray checkout free of violations kept it
    honest. Membership now asks git, through the same `_sources` the sibling
    guard in `test_workflows` reuses; a second implementation of repository
    membership is itself item 115's class.

    **The floor is a PAIR, and the first cut of this fix replaced one half of it
    with the other.** The count catches a corpus narrowed to almost nothing; the
    identity check catches a corpus made of somebody else's checkout. Measured
    at both ends: narrow the corpus to the one file named below and identity
    stays green while the count fires, and against the real pre-fix walk — 103
    real files and several hundred foreign ones — identity is green on the
    defect itself, because the genuine file IS in that corpus at its tracked
    path. Neither floor dominates the other, so both stay.
    """
    import re
    suffixed = re.compile(r"\{[^{}]*verdict[^{}]*\}(?:e?d)\b")

    def sites(text: str) -> list[int]:
        return [n for n, line in enumerate(text.splitlines(), 1)
                if not line.lstrip().startswith("#") and suffixed.search(line)]

    corpus = [(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
              for p in _sources(tests=False)]

    # Non-vacuity takes BOTH floors, because each is blind where the other sees.
    # BREADTH: a corpus of one file is a sweep of one file, and only a count
    # says so.
    assert len(corpus) > 20, f"the sweep gathered {len(corpus)} files, not a tree"
    # IDENTITY: a count is the instrument a FOREIGN corpus defeats, because any
    # number this floor could name is one a few hundred stray checkouts supply
    # on their own. What they cannot supply is the very module the rest of this
    # file imports, at the path git tracks it under, holding the bytes that were
    # loaded — a sibling worktree's copy answers to
    # `.tmp/wt-.../tools/sdlc/approve.py` and git in this repository does not
    # list it at all. Neither floor is the stronger; the pair is.
    mine = Path(A.__file__).resolve()
    under_test = mine.relative_to(ROOT).as_posix()
    gathered = [text for name, text in corpus if name == under_test]
    assert gathered == [mine.read_text(encoding="utf-8")], (
        f"the sweep did not gather {under_test} — the module these tests "
        f"import — so it is not looking at this repository: it holds "
        f"{len(gathered)} entr{'y' if len(gathered) == 1 else 'ies'} for that "
        f"path out of {len(corpus)} files")

    # The control rides IN the corpus rather than beside it, so blinding the
    # detector and blinding the walk both fail here. Asserting the regex works
    # on its own left both mutations alive: a sweep that finds nothing and a
    # sweep that looks at nothing hand back the same empty list.
    corpus.append(("planted.py",
                   'print(f"{gate} {verdict}d by {who}")\n'
                   '# {verdict}ed inside a comment is the RECORD of the defect\n'))

    offenders = [f"{name}:{n}" for name, text in corpus for n in sites(text)]
    assert offenders == ["planted.py:1"], (
        f"a past tense built by suffix is right for one verdict and wrong for "
        f"the other, and the planted line must be the ONLY hit: {offenders}")


# ── item 144: a verdict nobody can read is not one nobody gave ──────────────


def _truncate_the_verdict(repo: Path, run_id: str) -> None:
    """Damage the ledger the way a half-written append or a bad merge does:
    the line is there and it is no longer JSON."""
    path = A.approvals_path(repo, run_id)
    whole = path.read_text(encoding="utf-8")
    path.write_text(whole[:len(whole) // 2] + "\n", encoding="utf-8")


def test_a_verdict_nobody_can_read_is_not_a_verdict_nobody_gave(repo):
    """**Item 144, measured on a real recorded rejection.**

    `read_chain` returns an unparseable line as `{"_unreadable": n}`, which
    carries no gate, so the filter dropped it and `if not entries` answered
    about ABSENCE - while `verify_chain`, in the same module, names the damaged
    line exactly. The ledger's own tamper-evidence was asked one line too late.
    """
    rid = _run_at(repo, "P3")
    A.record(repo, rid, "G2", "reject", WHO, notes="not good enough")
    assert A.gate_state(repo, rid, "G2") == (False, f"G2 was rejected by {WHO}")

    _truncate_the_verdict(repo, rid)
    assert A.verify_chain(repo, rid), "the fixture did not actually damage it"
    ok, why = A.gate_state(repo, rid, "G2")
    assert ok is False
    assert "not readable JSON" in why, why
    assert "no entry in the ledger" not in why, (
        "a verdict this file can no longer read was reported as one nobody gave")


def test_a_run_with_no_ledger_at_all_still_says_the_gate_has_no_entry(repo):
    """The control, and it is the reason the fix is an ORDERING rather than a
    new sentence: nothing recorded is not damage, and it must keep its own
    words. Green by design at the pre-item SHA."""
    rid = _run_at(repo, "P3")
    assert not A.verify_chain(repo, rid)
    assert A.gate_state(repo, rid, "G2") == (False, "G2 has no entry in the ledger")


def test_a_gate_with_a_readable_entry_beside_a_damaged_one_already_said_so(repo):
    """The boundary, pinned. The defect bit only when EVERY entry for the gate
    was unreadable: one readable sibling made `entries` non-empty and the chain
    check then ran. Green by design - it is here so that a later 'simplification'
    that puts the filter back in front cannot pass by testing only this case."""
    rid = _run_at(repo, "P3")
    A.record(repo, rid, "G2", "reject", WHO, notes="not good enough")
    path = A.approvals_path(repo, rid)
    path.write_text(path.read_text(encoding="utf-8") + "{half a line\n",
                    encoding="utf-8")

    ok, why = A.gate_state(repo, rid, "G2")
    assert ok is False and "not readable JSON" in why, why


def test_the_sentence_a_phase_halts_with_carries_the_damage(repo):
    """**The composition, which is the gap this project finds more often than
    any other.** `gate_state` has tests and `stage.halted_at` has tests, and
    what a person actually reads at a gate is the second one quoting the first.
    A driver, a runner and an operator all meet the ledger through this
    sentence and never through `verify_chain`.
    """
    rid = _run_at(repo, "P3")
    A.record(repo, rid, "G2", "reject", WHO, notes="not good enough")
    _truncate_the_verdict(repo, rid)

    held = PHASES.halted_at(repo, rid, "P3")
    assert held, "G2 is hard under solo, so P3 must halt"
    assert "not readable JSON" in held[0], held[0]
