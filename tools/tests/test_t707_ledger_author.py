"""T707 - a ledger entry records the change's author, inside the signed body.

`approve.record` judged the author against the policy's `self_approval` rule and then
threw the value away, so separation of duties was enforced at one door and provable
from none. Now the entry carries `author`, `author_source` and the `self_approval`
mode, and `approve.py --verify` and the emitted ledger check judge each gate's live
approval by `separation_of_duties`.

Every behaviour is asserted by SPAWNING the real CLI (approve.py, and the emitted
`ledger_check.py` with no PYTHONPATH), because a CLI's contract is a process. The one
exception is the judge's own table (`test_the_judge_answers_every_cell`), which is a
pure function. Every test here fails on the pre-T707 code except the two named
`test_BACK_COMPAT_*`, which document that an old ledger keeps its bytes and its
verdict, and that a caller passing no author writes the old bytes - they pass before
and after, by design.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

APPROVE = ROOT / "tools" / "sdlc" / "approve.py"
LEAD = "lead@example.invalid"
DEV = "dev@example.invalid"
AT = "2026-11-07T09:00:00+00:00"

#: Two entries `append_entry` wrote on the pre-T707 code (HEAD of main before this
#: row), time and run id fixed - a reject then an approve, no author passed. Captured,
#: not computed: the bytes an old ledger holds.
PRE_T707 = (
    '{"approver": "lead@example.invalid", "artifacts": {"handoffs/design-1.md": '
    '"sha256:4b77879b2e269a4972b73cc8c461208a5c44b0447587df7c1f0162d852762963"}, '
    '"at": "2026-11-07T09:00:00+00:00", "gate": "design-approval", "hash": '
    '"sha256:28e62531eee3f6dddadd2ec70327b375a97b212f465b6b8ff8306594f2a08942", '
    '"kind": "approval", "notes": "pre-T707", "prev": "", "run_id": "r-t707-pre", '
    '"schema_version": 1, "verdict": "reject"}\n'
    '{"approver": "lead@example.invalid", "artifacts": {"handoffs/design-1.md": '
    '"sha256:4b77879b2e269a4972b73cc8c461208a5c44b0447587df7c1f0162d852762963"}, '
    '"at": "2026-11-07T09:00:00+00:00", "gate": "design-approval", "hash": '
    '"sha256:aa4be5192f47999668d2afb50d93f466bc0585067fe841c33c4be56d13079995", '
    '"kind": "approval", "notes": "pre-T707", "prev": '
    '"sha256:28e62531eee3f6dddadd2ec70327b375a97b212f465b6b8ff8306594f2a08942", '
    '"run_id": "r-t707-pre", "schema_version": 1, "verdict": "approve"}\n'
)


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", A.SIGNING_KEY_ENV)}
    env.update(extra)
    return env


def _spawn(argv: list[str], cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300,
                          env=_env(**env), cwd=str(cwd) if cwd else None)


def approve(repo: Path, rid: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return _spawn([str(APPROVE), "--target", str(repo), "--run", rid, *args], **env)


def verify(repo: Path, rid: str) -> subprocess.CompletedProcess:
    return approve(repo, rid, "--verify")


def last(repo: Path, rid: str) -> dict:
    return A.read_chain(repo, rid)[-1]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True, capture_output=True)
    return r


def forbid(repo: Path) -> None:
    (repo / ".sdlc" / "policy.yml").write_bytes(b"self_approval: forbid\n")


def _run_at(repo: Path, phase: str = "P4") -> str:
    st = L.create(repo, "requirement", "a change worth gating")
    (L.run_dir(repo, st["run_id"]) / "design.md").write_text("the plan", encoding="utf-8")
    L.record_artifact(repo, st["run_id"], "design.md")
    R.park(repo, st["run_id"], phase)
    return st["run_id"]


def _commit_as(repo: Path, email: str) -> None:
    (repo / "app.txt").write_text("work\n", encoding="utf-8")
    for args in (["add", "app.txt"],
                 ["-c", f"user.email={email}", "-c", "user.name=Someone", "commit", "-qm",
                  "work"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _handoff(repo: Path, run_id: str | None = None) -> tuple[str, dict]:
    kw = {"run_id": run_id, "now": datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)} \
        if run_id else {}
    rid = L.create(repo, "requirement", "a pipeline of its own", **kw)["run_id"]
    path = L.run_dir(repo, rid) / "handoffs" / "design-1.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"the design")
    return rid, {"handoffs/design-1.md": L.sha256_of(path)}


def _ledger_check(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    bundle = V.emit_check(tmp_path / "bundle" / "ledger_check.py")
    return _spawn([str(bundle), "--ledger", "--target", str(repo)], cwd=bundle.parent)


# ── 1-3: record writes the author it judged, and where it came from ──────────


def test_the_flag_author_is_recorded_with_its_source(repo):
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD,
                   "--author", f"  {DEV} ")
    assert done.returncode == 0, done.stdout + done.stderr
    entry = last(repo, rid)
    assert entry["author"] == DEV and entry["author_source"] == "flag"
    assert entry["self_approval"] == "allow"
    assert entry["hash"] == A.entry_hash(entry), "the author is inside the hashed body"


def test_with_no_flag_the_head_author_is_recorded_as_git_head(repo):
    _commit_as(repo, DEV)
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD)
    assert done.returncode == 0, done.stdout + done.stderr
    entry = last(repo, rid)
    assert (entry["author"], entry["author_source"]) == (DEV, "git-head")


def test_no_commit_under_allow_records_an_unknown_author(repo):
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD)
    assert done.returncode == 0, done.stdout + done.stderr
    entry = last(repo, rid)
    assert "author" in entry, "an unknown author is RECORDED as unknown, not omitted"
    assert (entry["author"], entry["author_source"]) == (None, "none")


def test_a_blank_author_under_forbid_is_refused_as_unknown(repo):
    """The value judged is the value recorded, stripped - so a blank `--author` is
    unknown at both, and `forbid` refuses it. Before T707 a whitespace author was
    truthy, compared unequal to every approver, and passed `forbid`."""
    forbid(repo)
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD,
                   "--author", "   ")
    assert done.returncode == 2 and "no recorded author" in done.stdout, done.stdout
    assert A.read_chain(repo, rid) == [], "nothing was written"


# ── 4: the signature binds the author ────────────────────────────────────────


def test_the_signed_payload_carries_the_author_and_an_edit_breaks_it(repo, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = tmp_path / "signing.pem"
    key.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    (repo / ".sdlc" / "profile.yml").write_text(
        "gate_profile: regulated\nsigning: dsse\n", encoding="utf-8")
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD,
                   "--author", DEV, **{A.SIGNING_KEY_ENV: str(key)})
    assert done.returncode == 0, done.stdout + done.stderr
    entry = last(repo, rid)
    payload = json.loads(base64.b64decode(entry["signature"]["dsse"]["payload"]))
    assert (payload["author"], payload["author_source"]) == (DEV, "flag")
    assert verify(repo, rid).returncode == 0

    # Edit ONLY the author and re-chain the hash, as a rewriter would: the chain
    # verifies, the signature does not.
    entry["author"] = LEAD
    entry["hash"] = A.entry_hash(entry)
    A.approvals_path(repo, rid).write_bytes(
        (json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    assert A.verify_chain(repo, rid) == []
    done = verify(repo, rid)
    assert done.returncode == 1 and "signature is over different bytes" in done.stdout, \
        done.stdout


# ── 5-7: the verifier judges each entry under the mode the ENTRY records ─────


def _restamp(repo: Path, rid: str, **fields) -> None:
    """Set `fields` on every entry and re-chain - what a modified client, or an edit
    whose author re-hashes, leaves behind. `record` itself refuses to write a
    self-approval under `forbid`, so this is the only way such an entry exists."""
    path = A.approvals_path(repo, rid)
    rows, prev = [json.loads(line) for line in
                  path.read_text(encoding="utf-8").split("\n") if line.strip()], ""
    for row in rows:
        row.update(fields)
        row["prev"] = prev
        row.pop("hash", None)
        row["hash"] = prev = A.entry_hash(row)
    path.write_bytes("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
                             for r in rows).encode("utf-8"))


def _approval(repo: Path, author, mode: str | None, gate: str = "G4") -> str:
    """One run holding one live approval by LEAD, with `author` (None = no author key)
    and `mode` (None = no self_approval key: the pre-T707 shape)."""
    rid, pins = _handoff(repo)
    A.append_entry(repo, rid, gate, "approve", LEAD, pins, author=author)
    if mode is not None:
        _restamp(repo, rid, self_approval=mode)
    return rid


def test_a_self_approval_recorded_under_forbid_fails_verify(repo):
    """Mutations: the same entry recorded under `allow`, and a different author under
    `forbid`, both pass. No policy file at all: the mode is the entry's own."""
    rid = _approval(repo, LEAD.upper(), "forbid")
    done = verify(repo, rid)
    assert done.returncode == 1, done.stdout
    assert "self-approval" in done.stdout and LEAD in done.stdout
    for author, mode in ((LEAD, "allow"), (DEV, "forbid")):
        done = verify(repo, _approval(repo, author, mode))
        assert done.returncode == 0 and "self-approval" not in done.stdout, done.stdout


def test_adopting_forbid_never_fails_history_recorded_under_allow(repo, tmp_path):
    """F1: the ordinary solo approval records author == approver under `allow`. The
    day the repository adopts `forbid`, that committed, append-only entry must not
    turn the checks red - an active run or an archived one."""
    _commit_as(repo, LEAD)
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD)
    assert done.returncode == 0, done.stdout + done.stderr
    entry = last(repo, rid)
    assert (entry["author"], entry["self_approval"]) == (LEAD, "allow")
    forbid(repo)
    done = verify(repo, rid)
    assert done.returncode == 0 and "self-approval" not in done.stdout, done.stdout
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0, done.stdout
    L.archive(repo, rid, force=True)
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0 and "self-approval" not in done.stdout, done.stdout


def test_an_approval_recorded_under_a_weaker_mode_is_stated_once_never_failed(
        repo, tmp_path):
    """R2-1 (owner, 2026-11-07): the recorded mode is the recording client's claim,
    so an approval taken while the policy said `allow` passes once it says `forbid` -
    and each verifier says so on ONE non-fatal line naming the entries."""
    _commit_as(repo, LEAD)
    rid = _run_at(repo)
    assert approve(repo, rid, "--gate", "G3", "--verdict", "approve",
                   "--approver", LEAD).returncode == 0
    forbid(repo)
    done = verify(repo, rid)
    assert done.returncode == 0, done.stdout
    said = [line for line in done.stdout.splitlines() if "weaker than policy" in line]
    assert len(said) == 1 and "1 live approval was recorded under self_approval: allow" \
        in said[0] and "entry 1 (G3)" in said[0], done.stdout
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0, done.stdout
    said = [line for line in done.stdout.splitlines() if "weaker than the policy" in line]
    assert len(said) == 1 and said[0].lstrip().startswith(".. [not checked]"), done.stdout
    bundle = tmp_path / "bundle" / "ledger_check.py"
    doc = json.loads(_spawn([str(bundle), "--ledger", "--target", str(repo), "--json"],
                            cwd=bundle.parent).stdout)
    assert doc["weaker"] == [f"{rid}: entry 1 (G3)"] and doc["problems"] == []
    # N2: the pointer is a whole sentence, with a remedy an adopter can run.
    assert said[0].rstrip().endswith("to name one run's entries"), said[0]
    assert "with `--json` to list each entry under weaker" in said[0], said[0]
    assert "where the harness is installed" in said[0], said[0]

    (repo / ".sdlc" / "policy.yml").write_bytes(b"self_approval: allow\n")
    assert "weaker" not in verify(repo, rid).stdout, "under allow nothing is weaker"


def test_an_unreadable_policy_is_one_limit_line_never_a_failure(repo, tmp_path):
    """R2-1 / F2: reading today's policy for the weaker-mode line never fails a run.
    `--verify` exits 0 with one stated line. The CI check's only problem is the
    pre-existing configuration note; this comparison adds a limit, not a problem."""
    (repo / ".sdlc" / "policy.yml").write_bytes(b"self_approval: forbid\ntypo_key: 3\n")
    rid = _approval(repo, DEV, "forbid")
    done = verify(repo, rid)
    assert done.returncode == 0 and "intact" in done.stdout, done.stdout
    said = [line for line in done.stdout.splitlines() if "policy could not be read" in line]
    assert len(said) == 1 and ".. [not checked]" in said[0], done.stdout
    bundle = V.emit_check(tmp_path / "bundle" / "ledger_check.py")
    doc = json.loads(_spawn([str(bundle), "--ledger", "--target", str(repo), "--json"],
                            cwd=bundle.parent).stdout)
    assert doc["problems"] == [doc["pattern_note"]], doc["problems"]
    assert sum("policy could not be read" in row for row in doc["limits"]) == 1


def test_the_ledger_check_prints_why_an_unreadable_policy_fails_it(repo):
    """Incidental (pre-existing): `pattern_note` was counted as a problem and never
    printed, so the check said FAILED with no reason on stdout. R2-4: the line names
    the file that failed, `policy.yml`, not the profile."""
    (repo / ".sdlc" / "policy.yml").write_bytes(b"self_approval: forbid\ntypo_key: 3\n")
    done = _spawn([str(ROOT / "tools" / "sdlc" / "validate.py"), "--ledger", "--target",
                   str(repo)])
    assert done.returncode == 1, done.stdout
    said = [line for line in done.stdout.splitlines() if "[configuration]" in line]
    assert len(said) == 1 and "typo_key" in said[0], done.stdout
    assert "policy.yml could not be read" in said[0], said
    assert "profile.yml could not be read" not in done.stdout, done.stdout


def test_the_not_judged_line_states_each_cause(repo, tmp_path):
    """R2-3: one line, but each cause counted under its own name - not the one cause
    an old ledger has."""
    gateless = _approval(repo, DEV, "forbid")
    _restamp(repo, gateless, gate="")
    _approval(repo, DEV, "maybe")
    _approval(repo, None, None)
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0, done.stdout
    said = [line for line in done.stdout.splitlines() if "not judged" in line]
    assert len(said) == 1, done.stdout
    for cause in ("for 3 live approvals", "1 record no self_approval mode",
                  "1 record a self_approval mode this harness does not know",
                  "1 name no gate"):
        assert cause in said[0], (cause, said[0])
    assert "typically" not in said[0]


def test_one_check_walks_each_ledger_for_separation_once(repo, monkeypatch):
    """R2-5: `separation_report` is computed once per run and handed to `integrity`,
    `limits` and the `--json` key, not once for each of them."""
    rid = _approval(repo, DEV, "forbid")
    calls = []
    real = A.separation_report
    monkeypatch.setattr(A, "separation_report",
                        lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    V.check(repo)
    assert calls == [rid]


def test_a_superseded_self_approval_is_not_judged_but_a_live_one_is(repo):
    """Live entries only - the last per gate - as the artifact pins are judged."""
    rid, pins = _handoff(repo)
    A.append_entry(repo, rid, "G4", "approve", LEAD, pins, author=LEAD)
    A.append_entry(repo, rid, "G4", "approve", LEAD, pins, author=DEV)
    _restamp(repo, rid, self_approval="forbid")
    assert verify(repo, rid).returncode == 0
    A.append_entry(repo, rid, "G4", "approve", LEAD, pins, author=LEAD)
    _restamp(repo, rid, self_approval="forbid")
    assert verify(repo, rid).returncode == 1


def test_an_entry_recording_no_mode_is_one_not_judged_line_and_never_fails(repo):
    rid = _approval(repo, None, None)
    for policy in (b"self_approval: forbid\n", b"self_approval: allow\n"):
        (repo / ".sdlc" / "policy.yml").write_bytes(policy)
        done = verify(repo, rid)
        assert done.returncode == 0, done.stdout
        said = [line for line in done.stdout.splitlines() if "not judged" in line]
        assert len(said) == 1, done.stdout
        assert "for 1 live approval " in said[0] and "entry 1 (G4)" in said[0], said
        assert "T707" not in done.stdout, "an adopter cannot resolve an internal row id"


def test_the_check_states_every_unjudged_approval_on_one_line(repo, tmp_path):
    """F3: two old runs, one archived - one summary line with the count, and
    `--json` names each; the line does not grow with the history."""
    first, second = _approval(repo, None, None), _approval(repo, None, None)
    L.archive(repo, first, force=True)
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0, done.stdout
    said = [line for line in done.stdout.splitlines() if "not judged" in line]
    assert len(said) == 1 and "for 2 live approvals" in said[0], done.stdout
    assert "T707" not in done.stdout
    bundle = tmp_path / "bundle" / "ledger_check.py"
    doc = json.loads(_spawn([str(bundle), "--ledger", "--target", str(repo), "--json"],
                            cwd=bundle.parent).stdout)
    assert sorted(row.split(":")[0] for row in doc["not_judged"]) == sorted([first, second])


def test_an_approval_naming_no_gate_is_stated_not_dropped(repo):
    """F7: an approve entry with no gate is no gate's live approval; it is named on
    the not-judged line rather than filtered out in silence."""
    rid = _approval(repo, DEV, "forbid")
    _restamp(repo, rid, gate="")
    done = verify(repo, rid)
    assert done.returncode == 0, done.stdout
    assert "names no gate" in done.stdout and "entry 1" in done.stdout, done.stdout


@pytest.mark.parametrize("case", ["self-approved", "not-judged", "ok"])
def test_the_emitted_ledger_check_reaches_the_same_verdicts(repo, tmp_path, case):
    author, mode = {"self-approved": (LEAD, "forbid"), "not-judged": (None, None),
                    "ok": (DEV, "forbid")}[case]
    rid = _approval(repo, author, mode)
    done = _ledger_check(repo, tmp_path)
    if case == "self-approved":
        assert done.returncode == 1 and "self-approval" in done.stdout, done.stdout
    elif case == "not-judged":
        assert done.returncode == 0, done.stdout
        assert "[not checked] separation of duties not judged for 1 live approval " \
            in done.stdout, done.stdout
    else:
        assert done.returncode == 0 and "not judged" not in done.stdout, done.stdout
    assert done.returncode == verify(repo, rid).returncode


# ── 8: an author in any script round-trips ───────────────────────────────────


def test_a_non_ascii_author_with_a_line_separator_round_trips(repo):
    author = "d" + chr(0xE9) + "v" + chr(0x2028) + chr(0x4E2D) + "@example.invalid"
    forbid(repo)
    rid = _run_at(repo)
    done = approve(repo, rid, "--gate", "G3", "--verdict", "approve", "--approver", LEAD,
                   "--author", author)
    assert done.returncode == 0, done.stdout + done.stderr
    chain = A.read_chain(repo, rid)
    assert len(chain) == 1 and chain[0]["author"] == author
    done = verify(repo, rid)
    assert done.returncode == 0 and "not judged" not in done.stdout, done.stdout


# ── 9: append_entry's author keyword, and the bytes it leaves alone ──────────


@pytest.mark.parametrize(("extra", "expect"), [
    ({"author": DEV}, "author= keyword"),
    ({"author_source": "flag"}, "own fields"),
    ({"self_approval": "forbid"}, "own fields"),
])
def test_an_author_smuggled_through_extra_is_refused(repo, extra, expect):
    rid, pins = _handoff(repo)
    with pytest.raises(ValueError, match=expect):
        A.append_entry(repo, rid, "G4", "approve", LEAD, pins, extra=extra)
    assert not A.approvals_path(repo, rid).exists()


def test_BACK_COMPAT_append_entry_without_an_author_writes_the_pre_t707_bytes(
        repo, monkeypatch):
    """DOCUMENTS - passes before and after T707, by design: a caller that never
    passes `author` keeps writing the bytes it wrote before."""
    monkeypatch.setattr(A, "_now", lambda: AT)
    rid, pins = _handoff(repo, "r-t707-pre")
    for verdict in ("reject", "approve"):
        A.append_entry(repo, rid, "design-approval", verdict, LEAD, pins, notes="pre-T707")
    assert A.approvals_path(repo, rid).read_bytes() == PRE_T707.encode("utf-8")


def test_append_entry_with_an_author_records_it_as_flag(repo):
    rid, pins = _handoff(repo)
    entry = A.append_entry(repo, rid, "G4", "approve", LEAD, pins, author=f" {DEV} ")
    assert (entry["author"], entry["author_source"]) == (DEV, "flag")
    assert "self_approval" not in entry, "append_entry judges nothing, so states no mode"


# ── 10: back-compat (DOCUMENTS - passes before and after T707, by design) ────


def test_BACK_COMPAT_a_pre_t707_ledger_still_verifies_unchanged(repo, tmp_path):
    rid, _ = _handoff(repo, "r-t707-pre")
    A.approvals_path(repo, rid).write_bytes(PRE_T707.encode("utf-8"))
    for entry in A.read_chain(repo, rid):
        assert entry["hash"] == A.entry_hash(entry), "an old entry's hash is unchanged"
    done = verify(repo, rid)
    assert done.returncode == 0 and "intact" in done.stdout, done.stdout
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0 and "OK - the record holds." in done.stdout, done.stdout


# ── 11: the judge, cell by cell ──────────────────────────────────────────────

_ABSENT = object()
BLANK = " " + chr(0x2028) + "\t"


def _entry(verdict: str, author, mode=_ABSENT) -> dict:
    e = {"verdict": verdict, "approver": LEAD}
    if author is not _ABSENT:
        e["author"] = author
    if mode is not _ABSENT:
        e["self_approval"] = mode
    return e


@pytest.mark.parametrize("mode", ["allow", "forbid", "maybe", None, _ABSENT])
@pytest.mark.parametrize("verdict", ["approve", "reject", "abstain"])
@pytest.mark.parametrize("author", [DEV, None, _ABSENT, LEAD, LEAD.upper() + " ", BLANK, 7])
def test_the_judge_answers_every_cell(mode, verdict, author):
    """Judged under the mode the ENTRY records (`mode` here is that recorded value;
    `_ABSENT` = no `self_approval` key, the pre-T707 shape)."""
    got, why = A.separation_of_duties(_entry(verdict, author, mode))
    assert got in A.SOD_VERDICTS and why and "T707" not in why
    if verdict == "reject":    # asked first: a rejection approves nothing, any mode
        want = "not-applicable"
    elif verdict != "approve":  # and an unknown verdict is not judged, any mode (N1)
        want = "not-judged"
    elif mode == "allow":
        want = "not-applicable"
    elif mode != "forbid":      # absent, null, or a mode this harness does not know
        want = "not-judged"
    elif author in (LEAD, LEAD.upper() + " "):
        want = "self-approved"
    elif author == DEV:
        want = "ok"
    else:                       # absent, null, blank, or not a string at all
        want = "not-judged"
    assert got == want, (mode, verdict, author, why)


def test_a_caller_with_a_policy_of_its_own_can_name_the_mode():
    """The optional `mode` judges under the caller's rule, not the entry's."""
    entry = _entry("approve", LEAD, "allow")
    assert A.separation_of_duties(entry)[0] == "not-applicable"
    assert A.separation_of_duties(entry, "forbid")[0] == "self-approved"


def test_the_judge_and_the_record_time_rule_are_one_rule():
    """F4: `self_approval` refuses, and the judge withholds a pass from, exactly the
    same authors - a blank one included - because both read `known_identity` and
    `same_person`."""
    from tools.sdlc import policy as P
    rules = {**P.DEFAULTS, "self_approval": "forbid"}
    for author in (LEAD, LEAD.upper(), f"  {LEAD}\t", DEV, "", "   ", BLANK):
        refused = bool(P.self_approval(rules, LEAD, author))
        judged = A.separation_of_duties(_entry("approve", author, "forbid"))[0]
        assert refused == (judged != "ok"), (author, judged)
        assert (P.known_identity(author) == "") == (judged == "not-judged"), author


def test_a_blank_author_is_refused_at_the_policy_cli_door(repo):
    """F4, the preview door: `policy.py --approver --author` reads a whitespace
    author as unknown and refuses under `forbid`, as `record` does."""
    forbid(repo)
    done = _spawn([str(ROOT / "tools" / "sdlc" / "policy.py"), "--target", str(repo),
                   "--approver", LEAD, "--author", "   "])
    assert done.returncode == 1 and "no recorded author" in done.stdout, done.stdout


# ── fix round 3 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["forbid", "allow"])
def test_a_live_entry_with_an_unknown_verdict_is_reported_not_dropped(repo, tmp_path, mode):
    """N1: a live entry whose verdict is neither approve nor reject - even a
    self-approval-shaped one, under either recorded mode - is counted on the
    not-judged line at every door, never filtered away as if it were a rejection."""
    rid = _approval(repo, LEAD, mode)
    _restamp(repo, rid, verdict="maybe")
    done = verify(repo, rid)
    assert done.returncode == 0, done.stdout
    assert "1 carry a verdict this harness does not know" in done.stdout, done.stdout
    assert "entry 1 (G4): verdict 'maybe'" in done.stdout, done.stdout
    done = _ledger_check(repo, tmp_path)
    assert done.returncode == 0, done.stdout
    assert "1 carry a verdict this harness does not know" in done.stdout, done.stdout


def test_every_not_judged_cause_is_one_a_verifier_reports():
    """N1: `NOT_JUDGED_CAUSES` names only causes `_judge` or `separation_report`
    can produce - each key is reachable from some entry."""
    reached = {A._judge(e, A.ENTRY_MODE)[1] for e in (
        {"verdict": "maybe"},
        {"verdict": "approve"},
        {"verdict": "approve", "self_approval": "maybe"},
        {"verdict": "approve", "self_approval": "forbid"},
        {"verdict": "approve", "self_approval": "forbid", "approver": LEAD},
        {"verdict": "approve", "self_approval": "forbid", "approver": LEAD,
         "author": "  "})} | {"no-gate"}
    assert reached == set(A.NOT_JUDGED_CAUSES)


def test_integrity_judges_separation_once_and_states_an_uncovered_run(repo, monkeypatch):
    """N4: `integrity` with no report computes separation once for the call (one
    report per run), and a report handed in that does not cover a run is a stated
    line, not a KeyError."""
    runs = [_approval(repo, DEV, "forbid") for _ in range(3)]
    calls = []
    real = A.separation_report
    monkeypatch.setattr(A, "separation_report",
                        lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    V.integrity(repo)
    assert sorted(calls) == sorted(runs)
    sod = V.separation(repo)
    late = _approval(repo, DEV, "forbid")
    said = V.integrity(repo, sod)
    assert [p for p in said if late in p] == [
        f"{late}: separation of duties was not judged - the report handed to this check "
        f"does not cover this run"]
