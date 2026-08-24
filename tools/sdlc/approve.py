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
    body = {k: v for k, v in entry.items() if k != "hash"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def read_chain(target: Path, run_id: str) -> list[dict]:
    """Every entry, in file order. An unreadable line is returned as a marker
    rather than skipped: a ledger that quietly drops a line it cannot parse is
    exactly the failure the chain exists to make impossible."""
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


def gate_state(target: Path, run_id: str, gate: str) -> tuple[bool, str]:
    """(is this gate satisfied right now, why not). The one question a runner
    asks before advancing, and the reason it may not."""
    entries = [e for e in read_chain(target, run_id) if e.get("gate") == gate]
    if not entries:
        return False, f"{gate} has no entry in the ledger"
    broken = verify_chain(target, run_id)
    if broken:
        return False, f"the ledger does not verify: {broken[0]}"
    last = entries[-1]
    if last.get("verdict") != "approve":
        return False, f"{gate} was {last.get('verdict')}ed by {last.get('approver')}"
    stale = verify_artifacts_for(target, run_id, last)
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
    blockers.append("DSSE envelope writing is not implemented yet (item 32 shipped the "
                    "hash chain; envelopes need a key and the extra in CI to be tested)")
    return blockers


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
    if mode == "dsse":
        why = ("profile asks for signing: dsse" if declared == "dsse"
               else f"gate_profile: {resolved['gate_profile']} requires signing: dsse")
        raise RuntimeError(f"{why}, which cannot be honoured here - "
                           + "; ".join(dsse_blockers(target)))

    chain = read_chain(target, run_id)
    if chain and "_unreadable" in chain[-1]:
        raise RuntimeError("the last ledger line is not readable JSON - repair it "
                           "before appending, or the chain starts lying here")
    entry = {
        "schema_version": SCHEMA_VERSION, "kind": "approval", "run_id": run_id,
        "gate": gate, "verdict": verdict, "approver": approver, "at": _now(),
        "phase": st.get("phase"), "gate_profile":
            intake_mod.profile_value(target, "gate_profile") or "unset",
        "signing": mode, "artifacts": dict(st.get("artifacts", {})),
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
    path = approvals_path(target, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    # Only a ledger entry unblocks — that is the whole mechanism.
    if verdict == "approve":
        ledger.advance(target, run_id, status="running",
                       note=f"{gate} approved by {approver}")
    else:
        ledger.advance(target, run_id, status="rejected",
                       note=f"{gate} rejected by {approver}")
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
        for e in read_chain(target, args.run):
            if "_unreadable" not in e:
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
    print(f"{entry['gate']} {entry['verdict']}d by {entry['approver']} "
          f"({entry['hash'][:19]}...)")
    print(f"  artifacts pinned: {len(entry['artifacts'])}")
    print(f"  ledger: {approvals_path(target, args.run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
