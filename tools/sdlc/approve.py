"""The approval ledger — item 32: a gate is a signed line in a file, or it did
not happen.

`approvals.jsonl` lives inside the run folder and is the CANONICAL record of
every gate decision, in every mode. There is one format and one code path: a
chat "approved" is an *input method* that ends up here, never a parallel
mechanism, because a gate recorded in two places is a gate recorded in
neither.

**Tamper-evidence costs zero dependencies.** Each entry carries the hash of
the one before it, computed with stdlib `hashlib` over the entry's own
canonical JSON. Editing any past line breaks every hash after it, and the
break names the line. That property is what survives into the solo,
dependency-free mode the whole harness is built to keep working in.

**What the chain does NOT do, said plainly.** Hashes without signatures detect
*edits*: change a past line and its own hash stops matching; recompute that
hash and the next line's `prev` stops matching. Someone who can rewrite the
whole file can also recompute every hash in it, and no unsigned chain can
notice. Two things answer that, and neither is pretended here: the ledger is
COMMITTED, so a rewrite is a diff someone reviews, and `signing: dsse` binds
entries to a key. Tamper-*evidence*, not tamper-proofing.

**An approval is of specific bytes.** The run's artifact hashes at the moment
of approval are copied into the entry, so `--verify` can answer the question
that actually matters at merge time: *is this still the thing that was
approved?* A post-approval edit does not silently keep its yes — the gate is
reported void, and the entry that voided it is named.

Deliberately NOT implemented here, and refused loudly rather than faked:
`signing: dsse`. Cross-person attribution needs in-toto DSSE envelopes over a
real key (`APPROVAL_SIGNING_KEY_PATH`), and the rule this project keeps is to
never hand-roll signature code. When a profile asks for `dsse`, approve.py
states exactly what is missing and refuses to record an entry that would look
attributed and not be.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import intake as intake_mod  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
APPROVALS = "approvals.jsonl"
VERDICTS = ("approve", "reject")
#: The past tense of each verdict, spelled rather than derived. **Item 135, and
#: it is this project's dominant shape in miniature.** Two sites built one by
#: SUFFIX and each was correct only for the verdict its author happened to be
#: testing: one printed `{verdict}d` - *approved* right, ***rejectd*** wrong -
#: and the other `{verdict}ed` - *rejected* right, ***approveed*** wrong. The
#: second is the worse one, because it is the REFUSAL telling an operator a gate
#: is already decided, so it read *"G1 was approveed by ..."* on the path where
#: somebody is being STOPPED and needs to trust the tool.
#:
#: It survived 5,043 tests because nothing ever asserted the SENTENCE, only the
#: verdict, and it needed a real rejection to surface: across three prior runs
#: the ledgers hold 19 approvals and ZERO rejections. A guard holds this table
#: against `VERDICTS`, so a third verdict fails here rather than in front of an
#: operator.
PAST_TENSE = {"approve": "approved", "reject": "rejected"}


def said(verdict: str) -> str:
    """A verdict as English, or as QUOTED DATA when it is not one this harness knows.

    The reader at `gate_state` walks a ledger this process did not write - the
    file is committed, hand-editable, and the whole point of the hash chain is
    that somebody might have - so an unrecognised word must not be dressed up in
    a sentence that reads like the harness understood it. Repeating it in quotes
    says where it came from.
    """
    if verdict in PAST_TENSE:
        return PAST_TENSE[verdict]
    return f"recorded with verdict {verdict!r}"

#: Which phase each gate stands at the end of — DERIVED from `stage.STAGES`, the
#: one place a gate's phase is spelled, so this map cannot drift from the SOPs.
#: Approving a gate a run has not reached yet is not an early yes, it is a yes
#: about work that does not exist.
GATE_PHASE = {s.gate: s.phase for s in stages.STAGES if s.gate}
GATES = tuple(GATE_PHASE)


def gate_phase_of(target: Path, gate: str) -> str:
    """The phase a gate stands at the end of, in THIS workspace's resolved pipeline.

    The RESOLVED pipeline is consulted first, not the static `GATE_PHASE` map,
    and T441c is why: a builtin gate whose step the profile deleted is
    re-anchored to an earlier active step, so G4 stands at review (P6) when
    `triage` is gone — and approving it must resolve to P6, or the phase check in
    `record` refuses it as "a yes about work that does not exist yet" because the
    run can never reach the static P7. An `extra_gates` gate (`G-<step>`) is only
    ever found here. The static map is the fallback for a gate the pipeline does
    NOT stand (a capability-dropped G1/G5): its original home is the honest
    answer to where it WOULD stand. A gate in neither is refused by name.
    """
    for g in stages.pipeline(target).gates:
        if g.id == gate:
            return g.after_phase
    if gate in GATE_PHASE:
        return GATE_PHASE[gate]
    raise ValueError(
        f"unknown gate {gate!r} - one of {', '.join(GATE_PHASE)}, or a G-<step> "
        f"gate this profile's extra_gates declares")
#: The env var naming the signing key, for the day `signing: dsse` is wired.
SIGNING_KEY_ENV = "APPROVAL_SIGNING_KEY_PATH"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def approvals_path(target: Path, run_id: str) -> Path:
    return ledger.run_dir(target, run_id) / APPROVALS


def entry_hash(entry: dict) -> str:
    """The entry's own hash: canonical JSON of everything EXCEPT the hash
    field, which cannot contain itself. Separators are pinned so the digest
    does not depend on how json.dumps happens to space things."""
    # `signature` is excluded for the same reason `hash` is: it is computed
    # FROM this digest, so covering it would be circular. Excluding it
    # changes no existing entry's hash - an unsigned entry has no such key -
    # so ledgers written before signing existed still verify unchanged.
    body = {k: v for k, v in entry.items() if k not in ("hash", "signature")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def read_chain(target: Path, run_id: str) -> list[dict]:
    """Every entry, in file order. An unreadable line is returned as a marker
    rather than skipped: a ledger that quietly drops a line it cannot parse is
    exactly the failure the chain exists to make impossible."""
    # NOT `cli.jsonl_rows`, and not by oversight. This module is one of the six
    # `closure()` embeds into the emitted `.sdlc/ledger_check.py`, whose loader
    # carries `tools.sdlc.*` and nothing else: a `from tools import cli` here
    # would be exec'd in an adopter's CI, where no harness exists, and the
    # required check would die on ImportError. The duplication is the price of
    # that file being dependency-free, and `test_ledger_gate.py` now fails if a
    # module in the closure reaches outside `tools.sdlc` at all.
    path = approvals_path(target, run_id)
    if not path.is_file():
        return []
    out = []
    # On the newline the writer joins with, never `splitlines()`, which also ends a line at
    # the U+2028, U+2029 and U+0085 a JSON string may hold raw: one entry read as several
    # unreadable ones, and an intact chain called itself broken (T635).
    for n, line in enumerate(path.read_text(encoding="utf-8",
                                            errors="replace").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            out.append({"_unreadable": n})
            continue
        out.append(row if isinstance(row, dict) else {"_unreadable": n})
    return out


def verify_chain(target: Path, run_id: str) -> list[str]:
    """Every break, named by position. Empty means the chain is intact — which
    is a statement about the FILE, not about the artifacts (see
    verify_artifacts_for)."""
    problems = []
    prev = ""
    for i, entry in enumerate(read_chain(target, run_id), 1):
        if "_unreadable" in entry:
            problems.append(f"entry {i}: not readable JSON - the chain cannot be "
                            f"walked past it")
            return problems
        if entry.get("prev", "") != prev:
            problems.append(f"entry {i} ({entry.get('gate', '?')}): links to "
                            f"{entry.get('prev', '')!r}, but the previous entry hashes "
                            f"to {prev!r} - an entry was edited, inserted or removed")
        recomputed = entry_hash(entry)
        if entry.get("hash") != recomputed:
            problems.append(f"entry {i} ({entry.get('gate', '?')}): its recorded hash "
                            f"does not match its contents - the line was edited")
        prev = entry.get("hash", "")
    return problems


def verify_artifacts_for(target: Path, run_id: str, entry: dict) -> list[str]:
    """What has changed since this entry APPROVED it. The question at merge
    time is not "was this approved" but "is this still what was approved".

    **Only an approval pins bytes this can speak about (item 138).** Every entry
    carries the run's artifact hashes at the moment it was written, a rejection's
    among them - but a rejection's pins mean "these bytes were REFUSED", not
    approved, and they are `stage.unanswered`'s subject rather than this one's.
    Checking them here reported a rejection's re-scoped pins as "changed since G3
    approved it" - a sentence about an approval that never happened - which
    failed `approve --verify` and `status --verify` on the ordinary
    reject->re-scope loop. A non-approval approved nothing, so nothing has
    "changed since it approved"; callers that already filter to approvals
    (deploy, learning) are unaffected, and `live_entries` still returns the
    current verdict of every gate for `stage.unanswered` to read.
    """
    if entry.get("verdict") != "approve":
        return []
    gate = entry.get("gate", "the gate")
    said = {"missing": f"approved at {gate}, now missing",
            "changed": f"changed since {gate} approved it"}
    # `drift_line`, so a file that changed only in its line endings still names
    # the gate whose approval it voids, and adds its remedy (review F5).
    return [ledger.drift_line(rel, kind, detail, said) for rel, kind, detail in
            ledger.drift(target, run_id, entry.get("artifacts", {}))]


def live_entries(target: Path, run_id: str) -> list[dict]:
    """The entries whose artifact pins still speak for a gate: the LAST one per
    gate, in chain order.

    Every entry stays in the chain and `verify_chain` still walks all of them —
    the hash chain is about the record, and rewriting history is what it exists
    to catch. This is the different question: **whose verdict is current**. A
    gate re-taken after the lifecycle's own P6-to-P4 loop supersedes the earlier
    one, and checking the earlier one's pins against today's tree reports a
    violation that is true and about nothing: it approved bytes the loop was
    supposed to replace. Measured on the first run to loop — an obsolete G3 held
    a correct change out of a merge.
    """
    return [entry for _, entry in live_positions(target, run_id)[0]]


def live_positions(target: Path, run_id: str) -> tuple[list[tuple[int, dict]],
                                                         list[tuple[int, dict]]]:
    """THE live-entry rule, in one place (T707): `([(position, entry)] for the last
    entry per gate in chain order, [(position, entry)] for every readable entry that
    names no gate)`. Positions are 1-based, as `verify_chain` names them. An entry
    with no gate is returned rather than dropped, so a caller can say it was not
    judged; `live_entries` keeps its behaviour by ignoring the second list (such an
    entry is no gate's verdict, and `gate_state` cannot be satisfied by it)."""
    latest: dict[str, tuple[int, dict]] = {}
    gateless: list[tuple[int, dict]] = []
    for i, entry in enumerate(read_chain(target, run_id), 1):
        if "_unreadable" in entry:
            continue
        gate = entry.get("gate")
        if isinstance(gate, str) and gate:
            latest[gate] = (i, entry)
        else:
            gateless.append((i, entry))
    return list(latest.values()), gateless


def about(entry: dict) -> str:
    """Which bytes an entry was a verdict ABOUT — its pinned artifact map, as one
    comparable string. Two verdicts with the same fingerprint were looking at the
    same work; two with different ones were not."""
    return json.dumps(entry.get("artifacts", {}), sort_keys=True)


def gate_state(target: Path, run_id: str, gate: str) -> tuple[bool, str]:
    """(is this gate satisfied right now, why not). The one question a runner
    asks before advancing, and the reason it may not.

    **A reject wins over an approve about the same bytes, whichever arrived
    last.** This was `entries[-1]` until item 42, which means arrival order
    decided: two reviewers looking at one diff disagree, and whoever wrote
    second won. On a shared branch that is a race, and it resolves the wrong
    way — the point of asking two people is that either may stop it.

    A reject does NOT block forever, and that is the other half. Work happens
    after a rejection; the approval that follows is pinned to DIFFERENT
    artifacts, so it is a verdict about different bytes and it stands. That is
    the plan's own wording — the pipeline halts "until a fresh approval is
    recorded against current artifact hashes" — and it is why the comparison is
    by fingerprint rather than by timestamp.

    Duplicate same-verdict entries are idempotent: the chain records that both
    happened, because it records what happened, and the state they describe is
    the same one.

    **Item 144: the chain is checked BEFORE anything is said about absence, and
    the order was the whole defect.** A verdict nobody can READ was reported as
    a verdict nobody GAVE.
    """
    # `read_chain` returns a line it cannot parse as `{"_unreadable": n}`, which
    # carries no gate - so the filter below dropped it, `if not entries` fired,
    # and a G2 rejection whose line had been truncated came back as *G2 has no
    # entry in the ledger*, while `verify_chain` - in this module, three lines
    # down - names the damaged line exactly. **Measured on a real recorded
    # rejection**: `gate_state` said no entry and `verify_chain` said *entry 1:
    # not readable JSON*. It bit only when EVERY entry for the gate was
    # unreadable, because one readable sibling made `entries` non-empty and the
    # check then ran; and nothing merged on it, since both answers are False and
    # item 44's CI gate calls `verify_chain` itself. **What it cost was what a
    # person is TOLD**, and the act that sentence invites is to take the gate
    # again over a damaged ledger rather than to repair it - which is the one
    # shape item 136 exists to stop, arriving through the file rather than the
    # prompt. Absent versus could-not-look, decided by ordering.
    if broken := verify_chain(target, run_id):
        return False, f"the ledger does not verify: {broken[0]}"
    entries = [e for e in read_chain(target, run_id) if e.get("gate") == gate]
    if not entries:
        return False, f"{gate} has no entry in the ledger"
    approvals = [e for e in entries if e.get("verdict") == "approve"]
    if not approvals:
        last = entries[-1]
        return False, (f"{gate} was {said(str(last.get('verdict')))} "
                       f"by {last.get('approver')}")
    latest = approvals[-1]
    contested = [e for e in entries if e.get("verdict") == "reject"
                 and about(e) == about(latest)]
    if contested:
        who_said_no = contested[-1].get("approver")
        return False, (f"{gate} was rejected by {who_said_no} over the same artifacts "
                       f"this approval is about - a reject wins over an approve about "
                       f"the same bytes whichever arrived last. Do the work, then "
                       f"approve again over what changed")
    stale = verify_artifacts_for(target, run_id, latest)
    if stale:
        return False, f"{gate} approved different bytes - {stale[0]}"
    return True, ""


def who(target: Path, given: str | None) -> str:
    """The approver. Taken from the flag, else the TARGET's configured git
    identity. Never invented: an approval attributed to nobody is not one.

    The target must be a repository for the second half to mean anything.
    `git config user.email` outside one still answers, from the machine's
    global configuration — so a gate taken against a workspace root, which is
    not itself a repository (item 41), was silently attributed to whoever
    happened to be logged in. An identity that has nothing to do with the
    target is worse than none, because none is a refusal and this was a name.
    """
    if given and given.strip():
        return given.strip()
    try:
        inside = subprocess.run(["git", "-C", str(target), "rev-parse",
                                 "--is-inside-work-tree"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=30)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return ""
        proc = subprocess.run(["git", "-C", str(target), "config", "user.email"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def signing_mode(target: Path) -> str:
    """`none` unless the profile asks for something else. Unset is not a
    silent `none` elsewhere in this harness, but here it is the honest
    default: a profile that never mentions signing is not asking for it."""
    return (intake_mod.profile_value(target, "signing") or "none").strip().lower()


def dsse_blockers(target: Path) -> list[str]:
    """Why `signing: dsse` cannot be honoured on this machine. Refusing with
    the list beats writing an entry that looks attributed and is not."""
    blockers = []
    try:
        import in_toto  # noqa: F401
    except ImportError:
        blockers.append("the in-toto library is not installed (optional `sdlc` extra) "
                        "- and signature code is never hand-rolled here")
    key = os.environ.get(SIGNING_KEY_ENV, "").strip()
    if not key:
        blockers.append(f"{SIGNING_KEY_ENV} is not set - there is no key to sign with")
    elif not Path(key).expanduser().is_file():
        blockers.append(f"{SIGNING_KEY_ENV} points at {key}, which does not exist")
    return blockers


#: The DSSE payload type for an approval. A verifier that does not recognise
#: this string should refuse rather than guess what it is holding.
PAYLOAD_TYPE = "application/vnd.sdlc-approval+json"


def signer_from(path: Path):
    """A signer from the PEM private key at `path`, or a refusal naming why.

    The key is read from the file `APPROVAL_SIGNING_KEY_PATH` names - never
    from the profile, never from an argument, and its value never reaches a
    record. `securesystemslib` does the signing: *never hand-roll signature
    code* is the recorded ADOPT rationale, and it is not revisited here.
    """
    from cryptography.hazmat.primitives import serialization
    from securesystemslib.signer import CryptoSigner

    try:
        pem = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise RuntimeError(f"the signing key at {path} could not be read: {exc}")
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        # An encrypted key is the likely case and earns its own sentence: the
        # harness has nowhere to put a passphrase that would not itself become
        # a secret in a file, so it refuses rather than inventing somewhere.
        raise RuntimeError(
            f"the signing key at {path} could not be loaded ({exc}). It must be "
            f"an unencrypted PEM private key - this harness has no place to hold "
            f"a passphrase that would not itself become a secret in a file")
    return CryptoSigner(key)


def sign_entry(entry: dict, signer) -> dict:
    """A DSSE envelope over the entry's canonical body.

    **What a signature here proves, stated because one invites over-reading:**
    the holder of this key signed these bytes. Whether that key belongs to the
    person named in `approver` is a question about key DISTRIBUTION, which this
    harness does not solve and must not appear to. The public key travels with
    the entry so an auditor can check the envelope is internally consistent and
    compare the keyid against a roster they trust separately.

    It EXTENDS the hash chain rather than replacing it. Item 32 recorded the
    honest limit of an unsigned chain - it detects an edit but not a wholesale
    rewrite, because a rewriter re-chains cleanly - and a rewrite without the
    key is exactly what this closes.
    """
    from securesystemslib.dsse import Envelope

    body = {k: v for k, v in entry.items() if k not in ("hash", "signature")}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    envelope = Envelope(payload=payload, payload_type=PAYLOAD_TYPE, signatures={})
    envelope.sign(signer)
    return {"dsse": envelope.to_dict(),
            "public_key": signer.public_key.to_dict(),
            "keyid": signer.public_key.keyid}


def verify_signatures(target: Path, run_id: str) -> list[str]:
    """Every signature problem, named by position. Empty means every signed
    entry verifies against the key it carries.

    **An entry claiming a signature this machine cannot check is a PROBLEM,
    never a pass.** Without this, a verifier lacking the library would report a
    signed ledger as fine having checked nothing - *nothing ran* spelled the
    same way as *nothing failed*, which is the shape this project keeps
    finding.
    """
    try:
        from securesystemslib.dsse import Envelope
        from securesystemslib.signer import Key
    except ImportError:
        signed = [i for i, e in enumerate(read_chain(target, run_id), 1)
                  if e.get("signature")]
        if not signed:
            return []
        return [f"{len(signed)} entr(ies) carry a signature and the signing "
                f"library is not installed here, so nothing checked them - "
                f"install the `sdlc` extra to verify, and do not read this as "
                f"a pass"]
    problems = []
    for i, entry in enumerate(read_chain(target, run_id), 1):
        block = entry.get("signature")
        if not block:
            continue
        gate = entry.get("gate", "?")
        try:
            key = Key.from_dict(block["keyid"], block["public_key"])
            envelope = Envelope.from_dict(block["dsse"])
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"entry {i} ({gate}): its signature block cannot be "
                            f"read ({exc}) - a signature nobody can parse is not "
                            f"a signature")
            continue
        if envelope.payload_type != PAYLOAD_TYPE:
            problems.append(f"entry {i} ({gate}): the envelope declares "
                            f"{envelope.payload_type!r}, not {PAYLOAD_TYPE!r}")
            continue
        # It must attest THIS entry, not merely be a valid envelope: a signature
        # lifted from another entry would otherwise verify perfectly.
        body = {k: v for k, v in entry.items() if k not in ("hash", "signature")}
        want = json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        if envelope.payload != want:
            problems.append(f"entry {i} ({gate}): the signature is over different "
                            f"bytes than the entry - the line was edited after it "
                            f"was signed, or the block came from elsewhere")
            continue
        try:
            envelope.verify([key], 1)
        except Exception as exc:                    # noqa: BLE001 - any failure
            problems.append(f"entry {i} ({gate}): the signature does not verify "
                            f"against the key it carries ({type(exc).__name__})")
    return problems


#: Where an entry's `author` came from (T707). `flag`: the caller named it (`--author`,
#: or `append_entry(author=...)`); `git-head`: the HEAD commit's author email, which is
#: what `record` judges when nothing was named; `none`: there was no value to record.
AUTHOR_SOURCES = ("flag", "git-head", "none")

#: The fields every entry carries itself; `append_entry`'s `extra` may not replace them.
#: T707 added `author`, `author_source` and `self_approval`: an `extra` carrying any of
#: them would be read by every verifier as a judged claim, which nothing judged.
CORE_FIELDS = frozenset({"schema_version", "kind", "run_id", "gate", "verdict", "approver",
                         "at", "artifacts", "notes", "prev", "hash", "signature",
                         "author", "author_source", "self_approval"})

#: The four answers `separation_of_duties` gives (T707).
SOD_VERDICTS = ("ok", "self-approved", "not-judged", "not-applicable")


def authorship(author: str | None, source: str) -> dict:
    """The `author` and `author_source` keys an entry records (T707): the value
    stripped, null when empty, and `none` as its source whenever there is no value -
    so `author: null` always reads `author_source: none`, whoever passed it."""
    if author is not None and not isinstance(author, str):
        raise ValueError(f"an author is an identity string, not {author!r}")
    if source not in AUTHOR_SOURCES:
        raise ValueError(f"unknown author source {source!r} - one of "
                         f"{', '.join(AUTHOR_SOURCES)}")
    value = guardrails.known_identity(author) or None
    return {"author": value, "author_source": source if value is not None else "none"}


#: `separation_of_duties`'s default: judge under the mode the ENTRY records.
ENTRY_MODE = object()


#: Every cause an approval can go unjudged for, as the predicate the summary line
#: counts it under ("2 record no self_approval mode"). Ordered as `_judge` tests them.
NOT_JUDGED_CAUSES = {
    "unknown-verdict": "carry a verdict this harness does not know",
    "no-mode": "record no self_approval mode",
    "unknown-mode": "record a self_approval mode this harness does not know",
    "no-approver": "name no approver",
    "no-author": "record no author",
    "unknown-author": "record the author as unknown",
    "no-gate": "name no gate",
}


def _judge(entry: dict, mode: object) -> tuple[str, str | None, str]:
    """(verdict, the `NOT_JUDGED_CAUSES` key when not judged, why).

    The verdict is asked FIRST (review N1): a rejection approves nothing under any
    mode, and a verdict this harness does not know is not judged under any mode -
    so it is reported even where the mode is `allow`, never read as a pass."""
    verdict = entry.get("verdict")
    if verdict == "reject":
        return "not-applicable", None, "a rejection approves nothing"
    if verdict != "approve":
        return ("not-judged", "unknown-verdict",
                f"verdict {verdict!r} is not one this harness knows")
    if mode is ENTRY_MODE:
        if "self_approval" not in entry:
            return "not-judged", "no-mode", ("the entry records no self_approval mode "
                                             "(written before entries recorded one)")
        mode = entry["self_approval"]
    if mode == "allow":
        return "not-applicable", None, "taken under self_approval: allow"
    if mode != "forbid":
        return "not-judged", "unknown-mode", f"unknown self_approval mode {mode!r}"
    approver = entry.get("approver")
    if not guardrails.known_identity(approver):
        return "not-judged", "no-approver", "the entry names no approver"
    if "author" not in entry:
        return "not-judged", "no-author", ("the entry records no author (written before "
                                           "entries recorded one)")
    author = entry.get("author")
    if not guardrails.known_identity(author):
        return "not-judged", "unknown-author", "the entry records the author as unknown"
    if guardrails.same_person(approver, author):
        return "self-approved", None, (
            f"{approver} is recorded as the change's author "
            f"({entry.get('author_source', 'source unrecorded')}) and approved it")
    return "ok", None, f"approved by {approver}, authored by {author}"


def separation_of_duties(entry: dict, mode: object = ENTRY_MODE) -> tuple[str, str]:
    """Whether this entry's approver approved their own change (T707). Stdlib only:
    it runs inside the emitted `.sdlc/ledger_check.py`.

    **The rule is the entry's own.** By default it is judged under the
    `self_approval` mode the entry itself RECORDS, never under today's policy file
    (owner, 2026-11-07). An entry taken under `allow` is never failed later, however
    the policy changes, so adopting `forbid` cannot turn committed, append-only
    history red. `mode` is for a caller judging the entry under a policy of its own;
    this harness never passes it.

    **The recorded mode is the recording client's claim**, exactly as the author is:
    it says which rule the client that wrote the entry enforced (signed with the rest
    of the body when signing is on), not which rule the repository holds. An approval
    taken under a relaxed policy - a working-tree edit, a modified client - is judged
    under that relaxed mode here. The control is enforced where the verdict is TAKEN;
    `weaker_than_policy` states, never fails, the approvals whose recorded mode is
    weaker than today's policy.

    `(verdict, why)`, verdict one of `SOD_VERDICTS`:

    * `not-applicable` - the mode is `allow`, or the entry is a rejection (a "no"
      cannot approve anything, one's own work included);
    * `not-judged` - there is nothing honest to compare; the causes are
      `NOT_JUDGED_CAUSES`. Never a pass and never a self-approval;
    * `self-approved` - under `forbid`, `policy.same_person(approver, author)`;
    * `ok` - under `forbid`, two different people.

    **What `ok` proves, and what it does not.** That the identity the approver's
    entry RECORDS as the author is not the approver. The author is a claim -
    `author_source` says whose (a flag, or the HEAD commit's git metadata) - and the
    free edition checks no commit signature, so a false author is not prevented. It
    is made attributable: on a signed ledger, the approver's key signed it.
    """
    verdict, _, why = _judge(entry, mode)
    return verdict, why


def current_mode(target: Path) -> tuple[str | None, str]:
    """(the `self_approval` mode today's policy sets, a limit line when it could not
    be read). Read ONLY to state approvals recorded under a weaker mode - never to
    judge one - so a policy that cannot be read is one stated line, never a failure."""
    try:
        return guardrails.load(target)["self_approval"], ""
    except Exception as exc:                        # noqa: BLE001 - stated below
        return None, (f"the policy could not be read ({type(exc).__name__}: {exc}), so "
                      f"approvals recorded under a weaker self_approval mode than it "
                      f"sets were not looked for")


def separation_report(target: Path, run_id: str, policy_mode: str | None = None) -> dict:
    """Separation of duties for the run's LIVE approvals - `live_positions`, the one
    live-entry rule - each judged by `separation_of_duties` under the mode the entry
    records (T707). One pass over the chain; callers that need it twice keep it.

    `problems`: a `self-approved` entry, recorded under `forbid` with its approver as
    its author - which `record` refuses, so it came from a modified client or an edit.
    `not_judged`: `(cause, line)` for each approval with nothing honest to compare
    (`NOT_JUDGED_CAUSES`), an approve entry naming no gate among them rather than
    dropped. `weaker`: when `policy_mode` (today's policy, from `current_mode`) is
    `forbid`, each live approval recorded under `allow` - stated, never a problem.
    `approvals`: how many live approvals there were to judge."""
    live, gateless = live_positions(target, run_id)
    out: dict = {"problems": [], "not_judged": [], "weaker": [], "approvals": 0}
    for i, entry in live:
        # Only a rejection is skipped (review N1): a verdict this harness does not
        # know is judged - as not judged, by cause - rather than filtered away.
        if entry.get("verdict") == "reject":
            continue
        out["approvals"] += 1
        verdict, cause, why = _judge(entry, ENTRY_MODE)
        gate = entry.get("gate")
        if verdict == "self-approved":
            out["problems"].append(f"entry {i} ({gate}): recorded under self_approval: "
                                   f"forbid, and {why} - a self-approval")
        elif verdict == "not-judged":
            out["not_judged"].append((cause, f"entry {i} ({gate}): {why}"))
        # O1: an entry already stated as not judged is not also "judged by its
        # recorded mode" on the weaker line - one entry, one line, every door.
        if (verdict != "not-judged" and policy_mode == "forbid"
                and entry.get("self_approval") == "allow"):
            out["weaker"].append(f"entry {i} ({gate})")
    for i, entry in gateless:
        if entry.get("verdict") != "reject":
            out["approvals"] += 1
            out["not_judged"].append(("no-gate", f"entry {i}: an approval that names no "
                                                 f"gate, so it stands for no gate"))
    return out


def not_judged_summary(pairs: list[tuple[str, str]], where: str) -> str:
    """The ONE line a verify or check run prints for every live approval it could
    not judge for separation of duties: the count, each cause's own count, and where
    the entries are named - one line however long the history."""
    counts = {c: sum(1 for k, _ in pairs if k == c) for c in NOT_JUDGED_CAUSES}
    causes = ", ".join(f"{n} {NOT_JUDGED_CAUSES[c]}" for c, n in counts.items() if n)
    return (f"separation of duties not judged for {len(pairs)} live "
            f"approval{'' if len(pairs) == 1 else 's'} ({causes}) - {where}")


def weaker_summary(lines: list[str], where: str) -> str:
    """The ONE line naming live approvals recorded under `self_approval: allow` while
    today's policy sets `forbid`. Never a problem: the recorded mode judges them."""
    return (f"{len(lines)} live approval{'' if len(lines) == 1 else 's'} "
            f"{'was' if len(lines) == 1 else 'were'} recorded under self_approval: "
            f"allow, weaker than the policy's forbid today; the recorded mode is the "
            f"recording client's claim and judges them, so they were not failed - {where}")


def append_entry(target: Path, run_id: str, gate: str, verdict: str, approver: str,
                 artifacts: dict, *, step: str = "", notes: str = "",
                 extra: dict | None = None, signer=None, forced: bool = False,
                 author: str | None = None) -> dict:
    """Append one hash-chained entry for ANY gate id, pinning the given artifacts,
    and emit `gate_decided` (T688, Z6).

    For a caller that runs a pipeline of its own - agentic-sdlc reuses this
    harness as a pinned library, with gates such as `design-approval` and
    artifacts such as `handoffs/design-1.md` - and so cannot use `record`,
    which stands on G1-G5, P-phases and `state.json`. Nothing here decides
    WHETHER the verdict may be recorded (phase reached, owed work, roles,
    four-eyes): the caller has decided that. What it guarantees is the record
    itself: a readable chain to link to, a hash over everything, an optional
    DSSE signature, and artifacts that are paths INSIDE the run folder, so
    `gate_state` voids the approval the moment one of them changes.

    `artifacts` maps a path inside the run folder to its digest (`sha256:...`,
    as `state.sha256_of` / `digest_of` write it). `extra` adds fields of the
    caller's own (they are hashed and signed with the rest) and may not replace
    the entry's own fields. `step` names the event's step.

    `author` (T707) is who wrote the change, as the CALLER claims it, recorded with
    `author_source: flag`. This function judges nothing, so it records no
    `self_approval` mode either - and the verifiers judge an entry only under the
    mode it records, so `approve.py --verify` and the ledger check count such an
    entry as not judged; a caller that enforces its own rule can judge it with
    `separation_of_duties(entry, mode)`. Not given, no author key is written at
    all, so a caller that never passes it writes the bytes it wrote before T707. An
    author smuggled in through `extra` is refused by name: unmarked, it would read
    as a judged one.
    """
    if "author" in (extra or {}):
        raise ValueError("extra cannot carry 'author' - pass it as the author= keyword, "
                         "which records where it came from")
    own = {} if author is None else authorship(author, "flag")
    if not isinstance(gate, str) or not gate.strip():
        raise ValueError("an approval names its gate; '' is not one")
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r} - one of {', '.join(VERDICTS)}")
    if not approver:
        raise RuntimeError("no approver - an approval attributed to nobody is not one")
    pins = {}
    for rel, digest in dict(artifacts).items():
        key = ledger.artifact_key(rel)  # refuses absolute paths, '..', '' and NUL
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError(f"artifact {rel!r}: a digest is 'sha256:<hex>', not {digest!r}")
        pins[key] = digest
    if clash := sorted(set(extra or {}) & CORE_FIELDS):
        raise ValueError(f"extra cannot replace the entry's own fields: {', '.join(clash)}")
    if not ledger.run_dir(target, run_id).is_dir():
        raise RuntimeError(f"no run folder for run {run_id} - an approval is about a run "
                           f"that exists")
    return _append_entry(target, run_id, gate, verdict, approver, pins, step=step,
                         notes=notes, extra={**dict(extra or {}), **own}, signer=signer,
                         forced=forced)


def _append_entry(target: Path, run_id: str, gate: str, verdict: str, approver: str,
                  artifacts: dict, *, step: str, notes: str, extra: dict, signer,
                  forced: bool) -> dict:
    """The one writer `record` and `append_entry` share: link, hash, sign, append,
    and the `gate_decided` event that points at the entry."""
    chain = read_chain(target, run_id)
    if chain and "_unreadable" in chain[-1]:
        raise RuntimeError("the last ledger line is not readable JSON - repair it "
                           "before appending, or the chain starts lying here")
    from tools.sdlc import events
    events.require_appendable(target, run_id)
    entry = {
        "schema_version": SCHEMA_VERSION, "kind": "approval", "run_id": run_id,
        "gate": gate, "verdict": verdict, "approver": approver, "at": _now(),
        "artifacts": artifacts, "notes": notes, **extra,
        "prev": chain[-1].get("hash", "") if chain else "",
    }
    entry["hash"] = entry_hash(entry)
    if signer is not None:
        # AFTER the hash and excluded from it: the envelope attests the same
        # canonical body the hash covers, so the two agree by construction
        # rather than by ordering luck.
        entry["signature"] = sign_entry(entry, signer)
    ledger.append_record(approvals_path(target, run_id),
                         json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    # T446a: the decision, in the audit trail - POINTING at this entry by its
    # hash rather than copying it, with the human time it waited: from the
    # `gate_waiting` it answers, or None when the run never halted there.
    # The wait this decision ANSWERS: the latest event about this gate, if it is
    # a wait. A wait an earlier decision already answered (a loop that came
    # back and was decided again without halting) is not this one's.
    latest = next((e for e in reversed(events.read(target, run_id))
                   if e.get("gate") == gate
                   and e.get("event") in (events.GATE_WAITING, events.GATE_DECIDED)),
                  None)
    waiting = latest if latest and latest["event"] == events.GATE_WAITING else None
    at = events.now()
    events.append(target, run_id, events.GATE_DECIDED, step, at=at,
                  gate=gate, decision=verdict, approver=approver,
                  ledger_hash=entry["hash"],
                  waited_ms=(events.millis_between(waiting["at"], at)
                             if waiting is not None else None),
                  forced=forced)
    return entry


def record(target: Path, run_id: str, gate: str, verdict: str, approver: str,
           notes: str = "", force: bool = False, author: str | None = None) -> dict:
    """Append one entry and move the run. Refuses more than it accepts, on
    purpose: this file is the record a merge is defended with."""
    gate_phase = gate_phase_of(target, gate)
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r} - one of {', '.join(VERDICTS)}")
    if not approver:
        raise RuntimeError("no approver: pass --approver, or set git config user.email "
                           "in the target - an approval attributed to nobody is not one")
    st = ledger.load(target, run_id)
    if st.get("status") in ledger.TERMINAL:
        raise RuntimeError(f"run {run_id} is {st['status']} - a terminal run takes no "
                           f"further approvals")
    reached = ledger.PHASES.index(st.get("phase", "P0"))
    if reached < ledger.PHASES.index(gate_phase) and not force:
        raise RuntimeError(
            f"run {run_id} is at {st['phase']} and {gate} stands at "
            f"{gate_phase} - approving it now would be a yes about work that "
            f"does not exist yet (--force records the override)")
    # Item 36: the phase check above asks whether the work has been REACHED.
    # This asks whether it EXISTS. Item 32 pins an approved artifact's hash so
    # a gate goes VOID when the bytes move — but nothing ever required a
    # phase's own output to be in that set, so the pin covered whatever
    # happened to be there, including nothing at all. A gate over an empty
    # artifact map is a yes about no bytes.
    # A phase the workspace does not run owes nothing — asking `scoping` for
    # `20-scope.md` in a repository with no graph would be demanding an artifact
    # from a phase that never happened.
    running = [s.phase for s in stages.active(target)]
    owed = (stages.verify(target, run_id, gate_phase)
            if gate_phase in running else [])
    if owed and not force:
        raise RuntimeError(
            f"{gate} stands at {gate_phase}, which has not delivered what "
            f"it owes, so there is nothing for this yes to be about: "
            + "; ".join(owed) + " (--force records the override)")
    # The guardrail policy (item 35). Checked HERE rather than trusted from the
    # runner, because a runner that skips the check is exactly the case the
    # policy exists for. A policy that cannot be read is a refusal: enforcing an
    # unreadable constitution is the same as having none.
    rules = guardrails.load(target)
    dials = guardrails.profile_dials(target)
    overrides = guardrails.gate_overrides(target)
    # T441c: the required_phases / required_gates floors compare against the
    # RESOLVED pipeline, which `policy` cannot read (the `policy`->`stage` cycle).
    # `approve` already imports `stage`, so this is the caller that resolves them
    # and hands them in — the enforcement point for a run actually being taken.
    # T470: the steps that RUN, not the phases the profile lists - a phase listed
    # but skipped for a missing capability satisfied the floor while running nothing.
    if loosened := guardrails.tighten_only(
            rules, dials, guardrails.component_count(target), overrides,
            phases=[s.name for s in stages.pipeline(target).steps],
            gates=stages.resolved_hard_gates(target)):
        raise RuntimeError("the profile loosens the repository's policy, so this "
                           "approval would be taken under rules nobody agreed to: "
                           + "; ".join(loosened))
    # T707: the author judged here is the author RECORDED below - one value, one git
    # call, stripped before it is judged so a blank one is unknown in both places.
    # It used to be judged and thrown away, which left separation of duties
    # enforced at this door and provable from nowhere.
    who_wrote = authorship(author if author is not None else guardrails.head_author(target),
                           "flag" if author is not None else "git-head")
    if refused := guardrails.self_approval(rules, approver, who_wrote["author"] or ""):
        raise RuntimeError(refused[0])

    declared = signing_mode(target)
    if declared not in guardrails.SIGNING_MODES:
        raise RuntimeError(f"unknown signing mode {declared!r} in .sdlc/profile.yml "
                           f"- one of {', '.join(guardrails.SIGNING_MODES)}")
    # The REGIME can require signing the profile never asked for: `regulated`
    # means dsse. Taking the profile's word would let a run declare a regime and
    # then write unsigned entries under it, which is the same hole the
    # tighten-only floor closes one level up.
    resolved = guardrails.effective(rules, dials, overrides)
    mode = resolved["signing"]
    signer = None
    if mode == "dsse":
        why = ("profile asks for signing: dsse" if declared == "dsse"
               else f"gate_profile: {resolved['gate_profile']} requires signing: dsse")
        if blockers := dsse_blockers(target):
            raise RuntimeError(f"{why}, which cannot be honoured here - "
                               + "; ".join(blockers))
        signer = signer_from(Path(os.environ[SIGNING_KEY_ENV].strip()))

    chain = read_chain(target, run_id)
    if chain and "_unreadable" in chain[-1]:
        raise RuntimeError("the last ledger line is not readable JSON - repair it "
                           "before appending, or the chain starts lying here")
    # T465: the audit trail is asked the same question, here, before the first
    # write. `gate_decided` is appended AFTER the feedback capture and the ledger
    # line below, and it was only there that a broken events chain refused - so
    # an approval reported as refused stood, and every retry recorded another
    # one in the append-only ledger that unblocks the gate.
    from tools.sdlc import events
    events.require_appendable(target, run_id)
    # Item 34, and the ordering is the whole of it. `st` was loaded at the top,
    # and its artifact map holds the hash from the moment the AGENT wrote each
    # file. A human who improved the design before approving it therefore
    # pinned bytes that no longer existed — an approval void the instant it was
    # written, about an edit the approver had just made themselves. Capturing
    # the human-vs-agent delta re-pins what they actually accepted, which is
    # why the state is re-read here: the entry below must copy the refreshed
    # map. A post-approval edit still voids the gate, which is item 32's
    # property intact and now aimed at the right bytes.
    #
    # Imported locally, for the reason `stage.verify` states about its judges:
    # feedback reads the ADR and stage contracts, and a module-level import
    # would make this file depend on the graph layer to record a verdict.
    from tools.sdlc import feedback as corrections
    corrections.capture(target, run_id, gate, verdict, approver, notes)
    st = ledger.load(target, run_id)
    # T688 (Z6): `record` builds the fields that are ITS OWN - phase, regime,
    # resolved gates, the override flags - and hands them to the one writer it
    # now shares with `append_entry`. Entries are serialised with sorted keys, so
    # the bytes and the hash are exactly what this function wrote before - plus,
    # by design, T707's three authorship keys;
    # `test_append_entry.test_record_writes_exactly_what_it_wrote_before` pins it.
    extra = {
        "phase": st.get("phase"), "gate_profile":
            intake_mod.profile_value(target, "gate_profile") or "unset",
        "signing": mode,
        # Which constitution was in force. An auditor reading this entry years
        # later should not have to guess whether a policy file existed yet.
        "policy_source": (found.name if (found := guardrails.locate(target))
                          else "defaults"),
        # DECLARED above (`gate_profile`), RESOLVED here. They differ whenever the
        # profile is silent or the policy floor raises it, and an auditor needs
        # both: what someone wrote, and what actually governed the run.
        "gate_regime": resolved["gate_profile"],
        "hard_gates": guardrails.hard_gates(rules, dials, overrides),
        # T441c: the RESOLVED hard gates for this workspace and where each
        # stands, as {gate id: phase}. `hard_gates` above is the REGIME set and
        # is kept unchanged for back-compat — it iterates the literal G1–G5 and
        # can neither hold an `extra_gates` `G-<step>` nor drop an orphan the
        # pipeline reconciled away. This is what CI reads: `validate.merge_ready`
        # demands every one of these the run REACHED, over bytes that have not
        # moved, so an added gate or a bypassed builtin can no longer be
        # invisible to the merge check. Stamped from the resolved pipeline;
        # deterministic (serialised sort_keys) and carries no build metadata.
        "resolved_gates": stages.resolved_hard_gate_map(target),
        # True for EITHER override — a yes about work not yet reached, or a yes
        # over a phase that never produced what it owed. Both are the same
        # sentence to whoever reads this line later.
        "forced": bool(force and (
            reached < ledger.PHASES.index(gate_phase) or owed)),
        "owed": owed,
        # T707: who wrote the change, as judged above, and where that came from -
        # inside the body, so the hash and any signature cover it - and the
        # `self_approval` mode that judged it, so an auditor need not read the
        # policy file as it stood that day. A claim, not proof: see
        # `separation_of_duties`.
        **who_wrote,
        "self_approval": rules["self_approval"],
    }
    # The artifacts THIS GATE is a verdict about, not the whole run. A gate
    # stands at the end of a phase and approves the work up to it; it has no
    # authority over a test report written afterwards. Pinning the whole map
    # meant a G3 taken after a P6-to-P4 loop pinned P5's and P6's documents,
    # and re-running those phases - the mandated next step - voided the gate
    # that had just authorised the fix. Everything recorded stays covered by
    # `state.verify_artifacts`, which asks the different question of whether
    # what was RECORDED still matches the tree.
    artifacts = {rel: digest for rel, digest in st.get("artifacts", {}).items()
                 if stages.governs(gate, rel)}
    entry = _append_entry(target, run_id, gate, verdict, approver, artifacts,
                          step=gate_phase, notes=notes, extra=extra, signer=signer,
                          forced=bool(force))

    # Only a ledger entry unblocks — that is the whole mechanism.
    if verdict == "approve":
        ledger.advance(target, run_id, status="running",
                       note=f"{gate} approved by {approver}")
    else:
        # HALTED, not `rejected`. Item 32 made a gate rejection terminal and item
        # 42 had to overturn it: the plan's concurrent-verdict rule is that the
        # "pipeline halts UNTIL A FRESH APPROVAL is recorded against current
        # artifact hashes", and a terminal run can never take that approval — so
        # the rule was unimplementable and, worse, one "no" at any gate killed
        # the run for good. Rejection at G2 or G3 is the ordinary case: the SOPs
        # say open findings loop back to design.
        #
        # `rejected` keeps its meaning and its terminality, as the operator's
        # separate act of ABANDONING a run (`state.py --advance --status
        # rejected`). "This change is not acceptable as written" and "this change
        # is not going to happen" are different decisions and now say so.
        ledger.advance(target, run_id, status="halted",
                       note=f"{gate} rejected by {approver} - the run halts until "
                            f"the work changes and a fresh approval is recorded")
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Record a gate decision in the run's canonical, hash-chained "
                    "approval ledger (item 32).")
    ap.add_argument("--target", required=True, metavar="DIR")
    ap.add_argument("--run", required=True, metavar="RUN_ID")
    # No static `choices`: an `extra_gates` id (G-<step>) is legal but known only
    # once the target's profile is resolved. `record` validates the gate against
    # THIS workspace's pipeline and refuses an unknown one by name.
    ap.add_argument("--gate")
    ap.add_argument("--verdict", choices=VERDICTS)
    ap.add_argument("--approver", default=None,
                    help="who is deciding (default: the target's git user.email)")
    ap.add_argument("--notes", default="")
    ap.add_argument("--force", action="store_true",
                    help="approve a gate the run has not reached; recorded as forced")
    ap.add_argument("--author", default=None,
                    help="who wrote the change, for policy self_approval, recorded "
                         "in the entry with author_source: flag (default: the "
                         "target's HEAD author, recorded as git-head)")
    ap.add_argument("--verify", action="store_true",
                    help="walk the chain, check signatures, re-hash approved artifacts "
                         "and judge each gate's live approval for separation of "
                         "duties under the self_approval mode THAT ENTRY records (a "
                         "self-approval recorded under forbid fails; today's policy "
                         "file does not judge; the recorded mode is the recording "
                         "client's claim); exit 1 on any problem. Approvals that cannot "
                         "be judged are counted by cause on one 'not judged' line, and "
                         "approvals recorded under allow while the policy now says "
                         "forbid on one 'weaker than policy' line - neither is a problem, "
                         "and an unreadable policy is a stated limit, never a failure")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2

    if args.verify:
        try:
            ledger.load(target, args.run)
        except (RuntimeError, ValueError) as exc:
            # T488: an unsafe run id is refused by `_safe_run_id` with a
            # ValueError, which this caught only as a traceback at exit 1 -
            # the code this CLI means "the ledger has problems".
            print(f"!! {exc}")
            return 2
        problems = list(verify_chain(target, args.run))
        # The signature half. This CLI runs where the harness runs, so it can
        # check envelopes; the emitted ledger check cannot and says so instead
        # of passing over them. Three separate questions, three answers: the
        # chain is about the FILE, the artifacts are about the TREE, and the
        # signature is about who wrote the line.
        problems += verify_signatures(target, args.run)
        # Live entries only - the last verdict per gate. See
        # `live_entries` for why a superseded pin is not a problem.
        for e in live_entries(target, args.run):
            problems += verify_artifacts_for(target, args.run, e)
        # T707: separation of duties, for the live approvals, each under the
        # self_approval mode its own entry records. A self-approval recorded under
        # `forbid` fails; what could not be judged is ONE line, never a failure.
        # Today's policy is read only to STATE approvals recorded under a weaker
        # mode; it never fails this run, readable or not.
        mode, unreadable = current_mode(target)
        sod = separation_report(target, args.run, mode)
        problems += sod["problems"]
        for p in problems:
            print(f"  !! {p}")
        if sod["not_judged"]:
            print("  .. [not judged] " + not_judged_summary(
                sod["not_judged"], "; ".join(line for _, line in sod["not_judged"])))
        if sod["weaker"]:
            print("  .. [weaker than policy] " + weaker_summary(
                sod["weaker"], "; ".join(sod["weaker"])))
        if unreadable and sod["approvals"]:
            print(f"  .. [not checked] {unreadable}")
        n = len(read_chain(target, args.run))
        print(f"ledger: {n} entr{'y' if n == 1 else 'ies'}, "
              + ("intact" if not problems else f"{len(problems)} problem(s)"))
        return 1 if problems else 0

    if not args.gate or not args.verdict:
        print("!! --gate and --verdict are required unless --verify is given")
        return 2
    try:
        entry = record(target, args.run, args.gate, args.verdict,
                       who(target, args.approver), args.notes, args.force,
                       author=args.author)
    except (ValueError, RuntimeError) as exc:
        print(f"!! {exc}")
        return 2
    print(f"{entry['gate']} {said(entry['verdict'])} by {entry['approver']} "
          f"({entry['hash'][:19]}...)")
    print(f"  artifacts pinned: {len(entry['artifacts'])}")
    print(f"  ledger: {approvals_path(target, args.run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
