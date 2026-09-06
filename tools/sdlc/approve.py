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

#: Which phase each gate stands at the end of — the master flow's own mapping.
#: Approving a gate a run has not reached yet is not an early yes, it is a yes
#: about work that does not exist.
GATE_PHASE = {"G1": "P2", "G2": "P3", "G3": "P4", "G4": "P7", "G5": "P9"}
GATES = tuple(GATE_PHASE)
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
    for n, line in enumerate(path.read_text(encoding="utf-8",
                                            errors="replace").splitlines(), 1):
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
    """What has changed since this entry approved it. The question at merge
    time is not "was this approved" but "is this still what was approved"."""
    gate = entry.get("gate", "the gate")
    said = {"missing": f"approved at {gate}, now missing",
            "changed": f"changed since {gate} approved it"}
    return [f"{rel}: {detail or said[kind]}" for rel, kind, detail in
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
    latest: dict[str, dict] = {}
    for entry in read_chain(target, run_id):
        if "_unreadable" in entry:
            continue
        gate = entry.get("gate")
        if isinstance(gate, str) and gate:
            latest[gate] = entry
    return list(latest.values())


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


def record(target: Path, run_id: str, gate: str, verdict: str, approver: str,
           notes: str = "", force: bool = False, author: str | None = None) -> dict:
    """Append one entry and move the run. Refuses more than it accepts, on
    purpose: this file is the record a merge is defended with."""
    if gate not in GATES:
        raise ValueError(f"unknown gate {gate!r} - one of {', '.join(GATES)}")
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
    if reached < ledger.PHASES.index(GATE_PHASE[gate]) and not force:
        raise RuntimeError(
            f"run {run_id} is at {st['phase']} and {gate} stands at "
            f"{GATE_PHASE[gate]} - approving it now would be a yes about work that "
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
    owed = (stages.verify(target, run_id, GATE_PHASE[gate])
            if GATE_PHASE[gate] in running else [])
    if owed and not force:
        raise RuntimeError(
            f"{gate} stands at {GATE_PHASE[gate]}, which has not delivered what "
            f"it owes, so there is nothing for this yes to be about: "
            + "; ".join(owed) + " (--force records the override)")
    # The guardrail policy (item 35). Checked HERE rather than trusted from the
    # runner, because a runner that skips the check is exactly the case the
    # policy exists for. A policy that cannot be read is a refusal: enforcing an
    # unreadable constitution is the same as having none.
    rules = guardrails.load(target)
    dials = guardrails.profile_dials(target)
    overrides = guardrails.gate_overrides(target)
    if loosened := guardrails.tighten_only(rules, dials,
                                           guardrails.component_count(target),
                                           overrides):
        raise RuntimeError("the profile loosens the repository's policy, so this "
                           "approval would be taken under rules nobody agreed to: "
                           + "; ".join(loosened))
    who_wrote = author if author is not None else guardrails.head_author(target)
    if refused := guardrails.self_approval(rules, approver, who_wrote):
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
    entry = {
        "schema_version": SCHEMA_VERSION, "kind": "approval", "run_id": run_id,
        "gate": gate, "verdict": verdict, "approver": approver, "at": _now(),
        "phase": st.get("phase"), "gate_profile":
            intake_mod.profile_value(target, "gate_profile") or "unset",
        # The artifacts THIS GATE is a verdict about, not the whole run. A gate
        # stands at the end of a phase and approves the work up to it; it has no
        # authority over a test report written afterwards. Pinning the whole map
        # meant a G3 taken after a P6-to-P4 loop pinned P5's and P6's documents,
        # and re-running those phases - the mandated next step - voided the gate
        # that had just authorised the fix. Everything recorded stays covered by
        # `state.verify_artifacts`, which asks the different question of whether
        # what was RECORDED still matches the tree.
        "signing": mode,
        "artifacts": {rel: digest for rel, digest in st.get("artifacts", {}).items()
                      if stages.governs(gate, rel)},
        # Which constitution was in force. An auditor reading this entry years
        # later should not have to guess whether a policy file existed yet.
        "policy_source": (found.name if (found := guardrails.locate(target))
                          else "defaults"),
        # DECLARED above (`gate_profile`), RESOLVED here. They differ whenever the
        # profile is silent or the policy floor raises it, and an auditor needs
        # both: what someone wrote, and what actually governed the run.
        "gate_regime": resolved["gate_profile"],
        "hard_gates": guardrails.hard_gates(rules, dials, overrides),
        # True for EITHER override — a yes about work not yet reached, or a yes
        # over a phase that never produced what it owed. Both are the same
        # sentence to whoever reads this line later.
        "notes": notes, "forced": bool(force and (
            reached < ledger.PHASES.index(GATE_PHASE[gate]) or owed)),
        "owed": owed,
        "prev": chain[-1].get("hash", "") if chain else "",
    }
    entry["hash"] = entry_hash(entry)
    if signer is not None:
        # AFTER the hash and excluded from it: the envelope attests the same
        # canonical body the hash covers, so the two agree by construction
        # rather than by ordering luck.
        entry["signature"] = sign_entry(entry, signer)
    path = approvals_path(target, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

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
    ap.add_argument("--gate", choices=GATES)
    ap.add_argument("--verdict", choices=VERDICTS)
    ap.add_argument("--approver", default=None,
                    help="who is deciding (default: the target's git user.email)")
    ap.add_argument("--notes", default="")
    ap.add_argument("--force", action="store_true",
                    help="approve a gate the run has not reached; recorded as forced")
    ap.add_argument("--author", default=None,
                    help="who wrote the change, for policy self_approval "
                         "(default: the target's HEAD author)")
    ap.add_argument("--verify", action="store_true",
                    help="walk the chain and re-hash approved artifacts; exit 1 on "
                         "any problem")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2

    if args.verify:
        try:
            ledger.load(target, args.run)
        except RuntimeError as exc:
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
        for p in problems:
            print(f"  !! {p}")
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
