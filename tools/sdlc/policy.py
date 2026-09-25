"""The guardrail policy layer — item 35: the rules are data, and the walls are
code in the CLIs.

`policy.yml` is the repository's constitution: the LOOSEST each dial may ever
be. `.sdlc/profile.yml` configures a run and may only TIGHTEN. That asymmetry
is the item. Without it, `review.block_on: []` in a profile turns the review
gate off and every run afterwards reports green — a control removed by editing
the file that configures it, which is no control at all. Loosening now costs a
change to `policy.yml`, which goes through the same gated run and the same
review as any other change.

**It reads with the standard library, because that is where it has to run.**
The harness's headline promise is a solo repo with no packages and no network,
and a guardrail that only works once PyYAML is installed is a guardrail that is
absent exactly where nobody would notice. So `policy.yml` is written in a small
YAML subset — top-level scalars and lists, one level of nesting — and anything
outside it is REFUSED by line number rather than guessed at. When PyYAML *is*
importable the two parses are cross-checked and a disagreement is an error: a
policy file that is misread is worse than one that is missing, because it looks
enforced.

**Fail-loud, never fail-open.** Every predicate here answers with the reasons it
found, and a control whose input is missing counts as unsatisfied. `self_approval:
forbid` with no author to compare against refuses the approval; it does not wave
it through. A missing `policy.yml` is the one benign absence — the built-in
defaults below are the floor — and even that is reported in `--show`.

Deliberately NOT here, and recorded rather than stubbed: `conftest`/Rego linting
from the task line. Rego is a second policy language, which the same plan
paragraph forbids ("policy stays data; no second policy language"), and it would
add an eighth vendored binary across five platforms to re-state what `validate()`
already checks. One implementation, exercised by the suite that runs everywhere.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# `intake` was imported here for ONE call — `profile_dials` asking its
# top-level profile reader for three keys. That reader was the EIGHTH copy of
# "what is a top-level key in this file", with its own answer, and item 117
# moved it to `profile_top_value` below, beside the one indentation rule.
# Deleting the import is the point rather than a tidy-up: `intake` stays in the
# emitted `.sdlc/ledger_check.py` bundle regardless (`approve.py` and
# `stage.py` both import it at column 0), so what this changes is the ORDERING
# constraint, not the membership — measured before it was removed.

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
POLICY_NAME = "policy.yml"

#: The adopter's document, named ONCE. Item 129: `policy.yml` has had a home
#: since item 35 and the profile - the file an adopter actually fills in - was
#: spelled inline in three modules, which is how `learning.py` sat outside the
#: one-read guard's hand-written roster while destroying block-form lists.
#: Item 94's rule, for the governing document rather than for an artifact.
PROFILE_NAME = "profile.yml"

#: Ordered loosest-first. A run's choice must sit at or after the floor.
GATE_PROFILES = ("solo", "team", "regulated")
SIGNING_MODES = ("none", "dsse")
SELF_APPROVAL = ("allow", "forbid")
#: Ordered loosest-first, so a floor of `high` is satisfied by `[critical, high]`
#: and not by `[critical]`.
SEVERITIES = ("info", "low", "medium", "high", "critical")

#: The five human gates, in lifecycle order.
GATES = ("G1", "G2", "G3", "G4", "G5")
#: Pre-merge approval. Spelled once here because three things ask about it — the
#: presets below, `approve.py`, and the CI ledger gate — and a merge gate two
#: files disagree about is worse than none.
MERGE_GATE = "G4"
GATE_STATES = ("hard", "advisory")

#: What each regime actually MEANS, mechanically. Before item 63 `gate_profile`
#: was a label nothing read: a profile could say `regulated` and behave exactly
#: like `solo`. These are the master flow's own words turned into data.
#:
#: `g3_granularity` is the one the plan is emphatic about: the regime sets HOW
#: FINELY agent changes are reviewed, never WHETHER they are. Under `solo` G3 is
#: not a halt, and the whole branch diff is still reviewed at G4 before anything
#: leaves it — which is why G4 is hard in every regime and may not be overridden.
PRESETS: dict[str, dict] = {
    "solo": {"hard_gates": ("G2", "G4"), "g3_granularity": "branch",
             "block_on": ("critical", "high"), "signing": "none"},
    "team": {"hard_gates": GATES, "g3_granularity": "diff",
             "block_on": ("critical", "high"), "signing": "none"},
    "regulated": {"hard_gates": GATES, "g3_granularity": "diff",
                  "block_on": ("critical", "high", "medium"), "signing": "dsse"},
}

#: The floor when a repository ships no `policy.yml`. These are the values the
#: harness already behaves by, written down — adopting the file changes nothing
#: until someone tightens it.
DEFAULTS: dict = {
    "schema_version": SCHEMA_VERSION,
    "gate_profile_floor": "solo",
    "block_on_floor": ["critical", "high"],
    "signing_floor": "none",
    "self_approval": "allow",
    "worktrees_root": ".sdlc-worktrees",
    # `feedback.jsonl` (item 34) belongs here for a reason the list did not
    # previously state: `learning_log.jsonl` was protected before anything
    # wrote it, and the file it is DISTILLED FROM was not. An agent that can
    # append to its own feedback record can manufacture the corrections a retro
    # agent learns from, and the protected conclusion then rests on unprotected
    # evidence.
    # `**/metrics/*.jsonl`, not a roster of filenames. The list named
    # `feedback.jsonl` and `learning_log.jsonl` individually, so every record
    # added afterwards had to be REMEMBERED onto it - and the one this project
    # added next was not. `provider-smoke.jsonl` became a gate the CLI answers
    # 0/1/3 from (`--status --milestone --require`) while staying writable by
    # any agent: a hand-written row claiming `runner_tier: agent, verdict:
    # green` made the recurring gate report satisfied for a smoke that never
    # ran. `reviews.jsonl`, which the M4 median is read from, and
    # `guardrail.jsonl`, which records refusals, were unprotected for the same
    # reason. The metrics directory is CLI-written; that is one rule, and a
    # record added tomorrow is covered by it without anyone remembering.
    "protected_paths": [".env", "**/.env", "**/policy.yml", "**/approvals.jsonl",
                        "**/state.json", "**/feedback.jsonl",
                        "**/learning_log.jsonl", "**/metrics/*.jsonl"],
    "push_remote": "origin",
    "push_branch_prefix": "sdlc/",
    "deny_git": ["--force", "-f origin", "push -f", "reset --hard", "clean -f",
                 "filter-branch", "filter-repo", "branch -D", "update-ref",
                 "reflog expire", "gc --prune"],
    # Destruction that git cannot undo, because it never reached git. `deny_git`
    # is git operations and says so in its name; until this list existed, the
    # only wall against `rm -rf` for ANY runner was the permission file the
    # installer writes for one of them. Matched by tokens, not substrings — see
    # `denied_command` for why that distinction is the whole item. One rule per
    # operation, spelled for every shell this harness runs under, because a rule
    # that only knows the POSIX spelling is not a rule on a Windows machine.
    # PowerShell accepts any unambiguous prefix of a parameter name, so
    # `-Recurse` is also `-recurse`, `-recu` and `-r`; the alternatives spell
    # the ones a person actually types. Measured: `ri -r -fo .` walked past a
    # rule that only knew the full word.
    "deny_commands": ["rm -r|-R|--recursive|-recurse",
                      "rmdir /s|-recurse|-r",
                      "rd /s|-recurse|-r",
                      "del /s",
                      "erase /s",
                      "remove-item -recurse|-recu|-r",
                      "ri -recurse|-recu|-r",
                      "find -delete",
                      "dd of=",
                      "mkfs",
                      "shred",
                      "sdelete",
                      # `truncate -s` knew ONE of the two ways GNU truncate
                      # spells its size. Measured: `truncate --size=0 FILE` and
                      # `truncate --size 0 FILE` both emptied a file with this
                      # wall watching. The `--size=` alternative is the same
                      # attached-value shape `dd of=` already uses.
                      "truncate -s|--size|--size="],
    "coverage_enforcement_floor": "advisory",
    "max_files_per_batch": 40,
    "max_lines_per_batch": 1500,
    "budget_exempt": ["**/*.lock", "**/package-lock.json", "**/uv.lock",
                      "**/poetry.lock", "**/Cargo.lock", "**/go.sum"],
    # Item 105: the org's runner floor. Empty = any declared runner may be
    # routed; named = a profile may only route WITHIN the list, the same
    # tighten-only shape as every floor above - an enterprise pins the
    # providers here and a developer's profile picks among them.
    "allowed_runners": [],
    # true = a routed runner must hold a GREEN provider-smoke row before a
    # phase is entered under it. False by default, deliberately: gates judge
    # whatever a runner leaves, so an unproven runner's output cannot pass a
    # gate on its own - conformance saves a wasted run, it does not guard
    # correctness, and a floor that guards nothing extra defaults open.
    "require_conformance": False,
    # T441c: the org's phase and gate floors, tighten-only like every dial above.
    # `required_phases` names steps a profile MUST run; `required_gates` names
    # gates it must keep as HARD stops — a builtin the regime made advisory, or an
    # extra_gates `G-<step>`. Both default to no floor, so adopting the keys
    # changes nothing until an org names something. Enforced by `tighten_only`
    # against the RESOLVED phases/gates its caller resolves, because this module
    # cannot read the pipeline itself (it is imported BY `stage`).
    "required_phases": [],
    "required_gates": [],
    # T454: where `metrics.py --export` writes when neither `--out` nor
    # SDLC_METRICS_OUT says. REPO-RELATIVE only - this file is committed, and an
    # absolute path is wrong on the next machine (the T360 defect class). Blank =
    # the default, `.sdlc/metrics/export/`.
    "metrics_out": "",
    # T452: how an EXPORT names the people in it. `email` (the default) writes
    # the approver's git identity as recorded; `hash` writes a stable
    # pseudonym instead, for an org that does not want personal identifiers in
    # an analytics store. The local ledger is untouched either way - this is
    # about what leaves the repository, not what the chain records.
    "identity": "email",
}

#: Where the constitution may live, in the order the resolver looks. `sdlc/` is
#: the plan's spelling for a repository that keeps lifecycle files together;
#: `.sdlc/` is where this harness's own installer writes everything else.
POLICY_LOCATIONS = (Path("sdlc") / POLICY_NAME, Path(".sdlc") / POLICY_NAME)

_COMMENT = re.compile(r"\s#")


def tab_refusal(name: str, line_no: int) -> str:
    """The one sentence either governing document gets for a tab.

    Both readers refuse a tab and item 117 made that two spellings of one rule —
    item 112's subject exactly, and item 115's: a restatement drifts, and the
    drift is silent because both messages still read like refusals. The mutation
    pass found the gap from the other side, by turning the profile's tab branch
    off and watching the suite stay GREEN: the per-character rule below catches
    a tab too, so the file was still refused, with `U+0009 (CHARACTER
    TABULATION), which looks like a space and is not` — a true sentence that
    tells a person the wrong thing about the most ordinary way to get this
    wrong. The branch earns its place by what it SAYS, so the saying is shared.
    """
    return f"{name}:{line_no}: tabs are not indentation in YAML"


def duplicate_key_refusal(name: str, line_no: int, key: str) -> str:
    """The one sentence either governing document gets for a duplicate top-level key.

    `tab_refusal`'s reason, one item along. `parse` has refused a duplicate key
    for `policy.yml` since item 35 (`if key in root`) and the profile reader did
    not until item 224, so a hand reader that took the FIRST and a PyYAML that
    took the LAST enforced different values silently. Two readers refusing one
    shape must SAY it once or the two spellings drift, and the drift is silent
    because both still read like refusals - so the saying is shared here.

    The DETECTION is not, and cannot cleanly be: `parse` builds a dict and
    catches the second key against it, while the profile reader walks lines and
    holds no dict. A guard test ties the two refusals to this one spelling
    instead (`test_both_readers_refuse_a_duplicate_key_the_same_way`).
    """
    return f"{name}:{line_no}: {key!r} is set twice - which one is the policy?"


class PolicyError(RuntimeError):
    """A policy that could not be read or does not validate. Never downgraded
    to a warning: the caller's next act would be to enforce it."""


class ProfileError(PolicyError):
    """A profile that is PRESENT and cannot be read, refused by line.

    `policy.yml` has been refused this way since item 35 and `.sdlc/profile.yml`
    never was, so the one document an adopter actually fills in was the one that
    got silently reinterpreted. Item 116 proved what that costs: one invisible
    character made seven hand readers give three different answers about the
    same file, and every consequence was PERMISSIVE — a component hidden from
    the `solo` structural check, a `G4: hard` override dropped, a component's
    test command returned as the workspace's.

    **A subclass of `PolicyError` deliberately.** Fourteen call sites across ten
    modules already catch `PolicyError` and turn it into `!! <reason>` at a
    non-zero exit; a fresh exception type would have walked past every one of
    them and arrived as a traceback, which is a worse answer than the silence it
    replaces. Callers that need to tell the two documents apart still can.

    **Absent is not unreadable.** No profile at all stays `[]` — a repository
    that has not been installed into is not a repository with a broken profile —
    and that distinction is the same one `absent`, `invalid` and `measured-zero`
    have needed everywhere else in this harness.
    """


# ── the restricted YAML subset, read with the standard library ───────────────


def _scalar(raw: str) -> object:
    """One value. Quoted text is taken verbatim; everything else is coerced the
    way YAML would, and an unquoted word stays a string."""
    text = raw.strip()
    if text[:1] in ('"', "'") and text[-1:] == text[:1] and len(text) >= 2:
        body = text[1:-1]
        # A single-quoted scalar has one escape, a doubled quote (T494's
        # crosscheck compares every quoted command against PyYAML, which reads
        # `'it''s'` as `it's`).
        return (body.replace('\\"', '"').replace("\\\\", "\\") if text[0] == '"'
                else body.replace("''", "'"))
    if text in ("", "~", "null"):
        return None
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment that is not inside quotes.

    Inside a DOUBLE-quoted value a backslash escapes the next character, so a
    `\\"` does not close the quote and a `#` after it is not a comment - the same
    escape `_scalar` decodes and `learning.scalar` emits (item T238). Without
    this the walker tracked quote state one character at a time and an escaped
    quote wrongly CLOSED the value, so a following ` #` was stripped and a
    finding carrying `" #` could not be written. A single-quoted value has no
    backslash escape in this subset, matching `_scalar`, so the escape is
    honoured only while the open quote is `"`.
    """
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
        i += 1
    return line


def _split_items(inner: str) -> list[str]:
    """Split an inline list on commas that are NOT inside a quoted item.

    `inner.split(",")` is the obvious way and it is wrong for any value that
    contains one: `["test_thing[1,2]"]` arrives as `['"test_thing[1', '2]"']`,
    two items neither of which matches anything. A parametrised test id is
    exactly that shape, and `known_failures` is exactly where somebody writes
    one by hand.

    Nothing the installer WRITES contains a comma, which is why this has never
    fired - and why it is the kind of defect that waits for the first person to
    hand-edit the file. Found by a test for the quarantine, one layer up.

    Deliberately not a real parser. This reader is a restricted subset that
    REFUSES what it cannot read, and the only thing needed here is to know
    whether a comma is inside quotes. An unterminated quote is left to
    `_scalar`, which takes an unquoted word as a string - the same answer the
    old splitter gave, so a malformed list does not change meaning.

    A backslash inside a DOUBLE-quoted item escapes the next character, so a
    `\\"` does not end the item and a comma after it is not a separator - the
    escape `_scalar` decodes and `learning.scalar` emits (item T238). Without
    it a value carrying `","` was torn into pieces neither reader wanted. Single
    quotes carry no such escape, matching `_scalar`; the escaped character is
    kept verbatim in the item for `_scalar` to decode.
    """
    items: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(inner):
        char = inner[i]
        if quote:
            buf.append(char)
            if char == "\\" and quote == '"' and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in ('"', "'"):
            quote = char
            buf.append(char)
        elif char == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(char)
        i += 1
    items.append("".join(buf))
    return items


def _inline_list(text: str) -> list:
    inner = text.strip()[1:-1].strip()
    return [_scalar(p) for p in _split_items(inner) if p.strip()] if inner else []


def parse(text: str, name: str = POLICY_NAME) -> dict:
    """The subset, or a refusal naming the line.

    Supported, and nothing else: `key: scalar`, `key: [a, b]`, `key:` followed
    by an indented block list of `- scalar`, and `key:` followed by one level of
    indented `subkey: scalar`. A policy file is small and the harness writes the
    template, so the cost of this restriction is nil and the benefit is that no
    shape is ever silently misread.
    """
    root: dict = {}
    holder: dict | list | None = None  # what the indented lines below belong to
    key = ""
    depth = 0  # the indent of the block under `key`, once one has been seen
    for n, original in enumerate(text.splitlines(), 1):
        line = _strip_comment(original).rstrip()
        if not line.strip():
            continue
        lead = line[:len(line) - len(line.lstrip())]
        if "\t" in lead:
            # Checked on the RAW leading whitespace. Counting only spaces made a
            # tab-indented line look top-level, so the refusal named the wrong
            # thing - right outcome, misleading reason, which for a policy file
            # is its own defect.
            raise PolicyError(tab_refusal(name, n))
        indent = len(lead)
        body = line.strip()

        if indent == 0:
            if body.startswith("- "):
                raise PolicyError(f"{name}:{n}: a list at the top level has no key")
            if ":" not in body:
                raise PolicyError(f"{name}:{n}: expected `key: value`, got {body!r}")
            key, _, rest = body.partition(":")
            key, rest = key.strip(), rest.strip()
            if not key:
                raise PolicyError(f"{name}:{n}: an entry with no key")
            if key in root:
                raise PolicyError(duplicate_key_refusal(name, n, key))
            if rest.startswith("[") and rest.endswith("]"):
                root[key] = _inline_list(rest)
                holder = None
            elif rest == "":
                root[key] = holder = {}  # a block list below replaces this
            else:
                root[key] = _scalar(rest)
                holder = None
            depth = 0
            continue

        # indented: it belongs to the last top-level key
        if not key:
            raise PolicyError(f"{name}:{n}: indented line before any key")
        # One level, and only one. Without this the reader would FLATTEN a deeper
        # document into the level above and enforce something nobody wrote - the
        # PyYAML cross-check catches it here, but the machines this has to be
        # right on are the ones without PyYAML.
        if depth and indent != depth:
            raise PolicyError(
                f"{name}:{n}: indented {indent} where the block under {key!r} is "
                f"indented {depth} - this policy reader supports one level of "
                f"nesting, and will not guess at a deeper one")
        depth = depth or indent
        if body.startswith("- "):
            if isinstance(holder, dict) and not holder:
                root[key] = holder = []
            if not isinstance(holder, list):
                raise PolicyError(f"{name}:{n}: a list item under {key!r}, which "
                                  f"already holds {type(holder).__name__}")
            holder.append(_scalar(body[2:]))
            continue
        if ":" not in body:
            raise PolicyError(f"{name}:{n}: expected `key: value` under {key!r}, "
                              f"got {body!r}")
        if not isinstance(holder, dict):
            raise PolicyError(f"{name}:{n}: a mapping under {key!r}, which already "
                              f"holds {type(holder).__name__}")
        sub, _, rest = body.partition(":")
        rest = rest.strip()
        if rest == "":
            raise PolicyError(f"{name}:{n}: {sub.strip()!r} under {key!r} has no "
                              f"value - a key opening a further block is the nesting "
                              f"this reader refuses rather than misreads")
        holder[sub.strip()] = (_inline_list(rest)
                               if rest.startswith("[") and rest.endswith("]")
                               else _scalar(rest))
    return root


def crosscheck(text: str, parsed: dict, name: str = POLICY_NAME) -> str:
    """Disagreement between this reader and PyYAML, when PyYAML is here.

    The stdlib reader is the one that runs everywhere, so it is authoritative —
    but where a real parser is available, letting the two differ silently would
    mean the policy means one thing on the maintainer's machine and another in
    the field.
    """
    try:
        import yaml  # noqa: PLC0415 - optional by design
    except ImportError:
        return ""
    try:
        reference = yaml.safe_load(text)
    except Exception as exc:  # a file YAML rejects is not a policy
        return f"{name}: PyYAML cannot read this file: {exc}"
    if reference == parsed:
        return ""
    keys = sorted(set(parsed) | set(reference or {}))
    differing = [k for k in keys if (parsed.get(k) != (reference or {}).get(k))]
    return (f"{name}: the standard-library reader and PyYAML disagree about "
            f"{', '.join(differing) or 'the document shape'} - the file uses YAML "
            f"this policy reader does not support, and a misread policy looks "
            f"enforced")


# ── loading and validating ───────────────────────────────────────────────────


def locate(target: Path) -> Path | None:
    root = Path(target)
    for rel in POLICY_LOCATIONS:
        if (root / rel).is_file():
            return root / rel
    return None


def validate(doc: dict, name: str = POLICY_NAME) -> list[str]:
    """Every reason at once, the way intake refuses — an operator fixing a
    policy file should need one round trip, not five."""
    problems = []
    unknown = sorted(set(doc) - set(DEFAULTS))
    if unknown:
        problems.append(f"unknown key(s): {', '.join(unknown)} - a policy key that "
                        f"nothing reads is a control someone thinks they have")
    version = doc.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        problems.append(f"schema_version {version!r}, but this harness writes "
                        f"{SCHEMA_VERSION}")
    for key, allowed in (("gate_profile_floor", GATE_PROFILES),
                         ("signing_floor", SIGNING_MODES),
                         ("coverage_enforcement_floor", ENFORCEMENT_MODES),
                         ("self_approval", SELF_APPROVAL)):
        if key in doc and doc[key] not in allowed:
            problems.append(f"{key}: {doc[key]!r} - one of {', '.join(allowed)}")
    if "block_on_floor" in doc:
        value = doc["block_on_floor"]
        if not isinstance(value, list) or not value:
            problems.append("block_on_floor must be a non-empty list - an empty floor "
                            "is a review gate that cannot fail")
        else:
            bad = [s for s in value if s not in SEVERITIES]
            if bad:
                problems.append(f"block_on_floor: unknown severit(ies) "
                                f"{', '.join(map(repr, bad))} - one of "
                                f"{', '.join(SEVERITIES)}")
    if "allowed_runners" in doc:
        value = doc["allowed_runners"]
        if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip() for v in value):
            problems.append("allowed_runners must be a list of runner names - "
                            "empty means any declared runner may be routed")
    # T454: an export destination a COMMITTED file names must be repo-relative -
    # an absolute path (POSIX, or a Windows drive) or one escaping the repository
    # is wrong on the next machine, refused by name. Blank means the default.
    if "metrics_out" in doc:
        value = doc["metrics_out"]
        if not isinstance(value, str):
            problems.append("metrics_out must be a path string, repo-relative")
        elif value.strip() and (value.strip().startswith(("/", "\\"))
                                or re.match(r"^[A-Za-z]:", value.strip())
                                or ".." in Path(value.strip()).parts):
            problems.append(f"metrics_out is {value!r} - it must be REPO-RELATIVE: this "
                            f"file is committed, and an absolute or escaping path is "
                            f"wrong on the next machine. A machine's own destination "
                            f"is --out or SDLC_METRICS_OUT")
    if "identity" in doc and doc["identity"] not in IDENTITY_MODES:
        problems.append(f"identity is {doc['identity']!r} - one of "
                        f"{', '.join(IDENTITY_MODES)}")
    # T441c floors, same shape as allowed_runners: a list of non-empty names,
    # empty meaning no floor.
    for key in ("required_phases", "required_gates"):
        if key in doc:
            value = doc[key]
            if not isinstance(value, list) or not all(
                    isinstance(v, str) and v.strip() for v in value):
                problems.append(f"{key} must be a list of names - empty means "
                                f"no floor")
    if "require_conformance" in doc and doc["require_conformance"] not in (
            True, False, "true", "false"):
        problems.append(f"require_conformance: {doc['require_conformance']!r} - "
                        f"true or false")
    for key in ("max_files_per_batch", "max_lines_per_batch"):
        if key in doc and (not isinstance(doc[key], int) or isinstance(doc[key], bool)
                           or doc[key] < 0):
            problems.append(f"{key}: {doc[key]!r} - a whole number, or 0 for no limit")
    for key in ("protected_paths", "deny_git", "deny_commands", "budget_exempt"):
        if key in doc and not isinstance(doc[key], list):
            problems.append(f"{key} must be a list")
    if isinstance(doc.get("deny_commands"), list):
        for rule in doc["deny_commands"]:
            # A rule with no program is a rule that matches nothing, and it
            # would sit in the constitution looking like a wall.
            if not str(rule).split():
                problems.append("deny_commands: an empty rule denies nothing - "
                                "remove it, or name the program it is about")
    for key in ("worktrees_root", "push_branch_prefix"):
        if key in doc and not isinstance(doc[key], str):
            problems.append(f"{key} must be text")
    if isinstance(doc.get("worktrees_root"), str) and (
            why := worktrees_root_problem(doc["worktrees_root"])):
        problems.append(why)
    if "push_remote" in doc:
        value = doc["push_remote"]
        if isinstance(value, list):
            if not value or any(not isinstance(v, str) or not v for v in value):
                problems.append("push_remote as a list must name at least one "
                                "non-empty remote - an empty set is not 'push "
                                "anywhere', it is a rule nothing can satisfy")
        elif not isinstance(value, str) or not value:
            problems.append("push_remote must be text, or a list of remotes for an "
                            "estate whose components publish to different places")
    if doc.get("push_branch_prefix") == "":
        problems.append("push_branch_prefix is empty - every branch would then be a "
                        "legal push target, which is the opposite of the rule")
    return [f"{name}: {p}" for p in problems]


def worktrees_root_problem(value: str) -> str:
    """Why this `worktrees_root` cannot hold a run's trees, or "".

    **T497.** The trees go in `<repository>/<worktrees_root>`, and
    `worktree.claim_root` makes git blind to that directory with a `*` rule in
    its `.gitignore`. A blank root or `.` IS the repository, so that rule landed
    in the repository's own `.gitignore` and ignored everything in it; an
    absolute root, or one that climbs out with `..`, puts the trees - and the
    rule - somewhere the repository does not own. Decided from the text alone,
    so the constitution is refused before anything reads it for a path.

    **Any `..` segment is refused, not only one that climbs out** - `metrics_out`'s
    rule (T454). A root that steps back in (`trees/..`) is the repository again,
    and "never `..`" is a rule the policy schema can state in a pattern, where
    counting how far a path climbs is not.
    """
    text = value.strip().replace("\\", "/")
    if not text:
        return ("worktrees_root is empty - the trees would be made in the "
                "repository's own root, and its .gitignore would be told to ignore "
                "everything")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return (f"worktrees_root {value!r} is absolute - it must be a directory "
                f"inside the repository, such as .sdlc-worktrees")
    parts = text.split("/")
    if ".." in parts:
        return (f"worktrees_root {value!r} steps out with `..` - it must be a "
                f"directory inside the repository, such as .sdlc-worktrees")
    if all(part in ("", ".") for part in parts):
        return (f"worktrees_root {value!r} is the repository's own root - the "
                f"trees need a directory of their own, such as .sdlc-worktrees, or "
                f"the repository's .gitignore is told to ignore everything")
    return ""


def load(target: Path) -> dict:
    """The repository's policy, defaults filled in. Raises rather than returning
    a partial one: every caller's next act is to enforce it."""
    path = locate(target)
    if path is None:
        return dict(DEFAULTS)
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.name
    doc = parse(text, name)
    if disagreement := crosscheck(text, doc, name):
        raise PolicyError(disagreement)
    if problems := validate(doc, name):
        raise PolicyError("; ".join(problems))
    return {**DEFAULTS, **doc}


# ── the tighten-only floor ───────────────────────────────────────────────────


def component_count(target: Path) -> int:
    """How many components this workspace declares.

    One per sequence item at the block's own indent - the indent of its first
    `- ` row. T469: this counted `- name:` rows, so an entry that wrote another key
    first (`- path: ../billing`, then `name: billing` - the same YAML) was not
    counted, and `solo`, which a multi-component estate may not select, passed on
    two components. `components` below finds its entries the same way.
    """
    leads = [len(line) - len(line.lstrip()) for line in _profile_block(target, "components")
             if _is_item(line.strip())]
    return sum(1 for lead in leads if lead == leads[0])


def _is_item(body: str) -> bool:
    """A block-sequence row: `-` alone, or `- ` and what the item holds."""
    return body == "-" or body.startswith("- ")


#: The keys a component's `vcs:` block may carry. Anything else is a typo that
#: would otherwise be read as "not set" - and "not set" for `deny_raw_git` is
#: the permissive answer, which is the wrong way for a typo to fail.
VCS_KEYS = ("commit_command", "push_command", "push_remote", "branch_pattern",
            "deny_raw_git", "notes")
COMPONENT_ROLES = ("authoritative", "legacy", "fork", "vendor")


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _inline_vcs(where: str, value: str) -> str:
    """The refusal for a `vcs:` written inline with something in it (T495).

    This reader reads `vcs:` as a block, one key per line. A top-level flow
    mapping was always refused by the line check; one nested in a component
    entry was read as nothing - `deny_raw_git: true` with it, so the harness
    drove raw git in the very repository that forbids it. `vcs: {}`, the
    spelling the installer's own comment shows, says nothing and still reads
    as nothing.
    """
    return (f"{where}: `vcs:` is written inline ({value.strip()}) - write it as a "
            f"block, one key per line beneath `vcs:`. An inline one is not read, "
            f"and dropping it would drop `deny_raw_git` with it")


def _text(raw: str) -> str:
    """A value read as TEXT: quoted text decoded exactly as `_scalar` decodes
    it, anything else as written.

    **T494.** The readers of component, runner and `vcs:` commands used
    `_unquote`, which strips the quotes and keeps the YAML escapes inside them.
    The installer writes a command through `_yaml_dq`, which escapes every
    quote and backslash, so a quoted Windows program path reached the shell
    with its quotes escaped and its backslashes doubled - the wrong program -
    while PyYAML read the same line correctly. Not `_scalar` itself: that also
    turns `80` and `true` into a number and a boolean, and these callers expect
    text.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return str(_scalar(text))
    return text


#: Per-component keys this reader carries out of the profile. `path` and `role`
#: were the only two for four items, while `render_components` was writing the
#: build and test declarations beside them into every estate profile - read by
#: nothing, because the sole reader anywhere asked for the TOP-LEVEL
#: `test_command`, which a multi-component workspace does not have (item 38).
#: Every per-component key this reader will carry. **It must cover every
#: command `sdlc-init.py` emits per component**, and `deploy_command` was
#: missing from it for as long as P9 has existed: the installer writes one
#: into every estate profile and the only reader of that profile dropped it,
#: so a component's own deploy was silently the top-level one, or nothing.
#: Item 38's headline finding one field along - the sweep that fixed
#: `test_command` hardened the fields it was looking at and left this one,
#: because there was no deploy phase yet to notice. A test now holds the
#: writer's list and this one together.
COMPONENT_KEYS = ("path", "role", "stack", "build_command", "test_command",
                  "coverage_command", "lint_command", "coverage_threshold",
                  "deploy_command", "known_failures", "sca_known_findings",
                  "probes")

#: The component keys whose value is a SEQUENCE. Item 130: `components()` is
#: line-based, so `known_failures:` with items beneath it stored the empty
#: string and the `- "x"` rows - which carry no colon - were dropped on the
#: floor. `suite.quarantined` then read blank as "nobody declared one" and
#: substituted the WORKSPACE list, so a component lost its own declaration and
#: inherited one nobody accepted for it. That is the spelling
#: `sca.py --propose-quarantine` drafts and the template teaches.
COMPONENT_LIST_KEYS = ("known_failures", "sca_known_findings", "probes")

#: Top-level blocks whose body is a SEQUENCE OF MAPS, so a key inside one
#: belongs to an entry rather than to the workspace. Item 130: `_profile_entry`
#: skipped `components` alone, and `runners` is the profile's other block of
#: this shape - so a runner's `phases:` self-scope was returned as the
#: WORKSPACE's `phases[]` and `stage.active()` answered `['P4']`, dropping
#: every gate, the review loop and closeout while reporting it had read the
#: profile's own list.
SEQUENCE_BLOCKS = ("components", "runners")

#: advisory < hard. Whether a missed changed-file coverage threshold is a
#: finding the reader weighs, or something the phase owes.
#: T452: how an export names people - `hash` pseudonymises the approver.
IDENTITY_MODES = ("email", "hash")

ENFORCEMENT_MODES = ("advisory", "hard")


def components(target: Path) -> list[dict]:
    """Every component the profile declares, with its `vcs` block resolved.

    Read from the shape `render_components` writes, for the reason
    `component_count` already states: this module's reader deliberately refuses
    a list of maps, and the generator owning the contract on both sides is
    cheaper and more honest than a second YAML parser guessing at it.

    A single-repository workspace declares no components. It gets ONE, named
    for the repository directory and rooted at it, carrying the top-level `vcs`
    block - the degenerate case of the same model, so nothing downstream needs
    to know which kind of workspace it is in.
    """
    lines = _profile_lines(target)
    found = _component_entries(lines)
    if found:
        unnamed = [n for n, comp in enumerate(found, 1) if not comp["name"]]
        if unnamed:
            # T469: read without a name, an entry could only be dropped or merged
            # into another - a silent wrong answer about the estate either way.
            raise ProfileError(
                f"{_profile_source(target)[0]}: components entry {unnamed[0]} has no "
                f"`name:` - a component is known by its name; add one")
        return found
    return _one_component(target, lines)


def _component_entries(lines: list[str]) -> list[dict]:
    """The `components:` block's entries, as read - from lines a caller holds.

    Factored out of `components` (T494) so the profile crosscheck can compare
    what THIS reader says each entry holds with what PyYAML says, from the same
    lines and without re-entering the door (`_profile_lines`) it stands in.
    """
    found: list[dict] = []
    in_vcs = False
    # `in_vcs` is reset by a TOP-LEVEL line as well as by the next `- name:`,
    # and losing that reset is item 116's own regression: the pre-116 loop
    # walked every line and cleared the flag whenever the indent went to zero,
    # while `_profile_block` yields only the block's own lines, so an
    # intervening top-level key never reached the loop to clear it. The cost is
    # a silent DROP — a component key after a `vcs:` sub-block is matched
    # against VCS_KEYS, misses, and disappears — which is this project's most
    # expensive direction for a reader to be wrong in.
    vcs_depth = -1
    pending, pending_depth = "", -1
    item_lead = -1
    for block, line in _profile_sections(lines):
        lead = len(line) - len(line.lstrip())
        # Item 221: a BLOCK HEADER resets, and so does a line inside no block -
        # but a `- name:` row at column 0 is neither, and testing `_indented`
        # here skipped the component's own first line, which is how a
        # zero-indent sequence reached the synthesised fall-back below.
        if _names_block(line) or not block:
            in_vcs, vcs_depth = False, -1
            pending, pending_depth = "", -1
            continue
        if block != "components":
            continue
        body = line.strip()
        # **Item 130: a sub-block ends where the INDENTATION says it does.**
        # `in_vcs` was cleared by a top-level line or by the next `- name:`, and
        # a component key written AFTER its own `vcs:` block is still indented -
        # so the flag stayed set, the key was matched against VCS_KEYS, missed,
        # and was DROPPED. PyYAML returns it. Measured cost: `suite.declared`
        # then reports `test: ('', '')` and path `.`, so P5 runs the suite at the
        # WORKSPACE ROOT and P9 loses its deploy command. The comment this
        # replaces called that "this project's most expensive direction for a
        # reader to be wrong in" while leaving the case open.
        if in_vcs and lead <= vcs_depth:
            in_vcs = False
        # **T469: an entry is a sequence item at the block's own indent** - the
        # indent of its first `- ` row - whichever key it writes first. Only
        # `- name:` opened one, so `- path: ../billing` / `name: billing` (the same
        # YAML) was folded into the entry before it, which took billing's test
        # command, and `solo` passed on two components. The item's first key is
        # read below like any other, at the column it actually sits in.
        if _is_item(body) and item_lead in (-1, lead):
            item_lead = lead
            found.append({"name": "", "path": "", "role": "authoritative", "vcs": {}})
            in_vcs, vcs_depth = False, -1
            pending, pending_depth = "", -1
            rest = body[1:]
            lead += 1 + len(rest) - len(rest.lstrip())
            body = rest.strip()
            if not body:
                continue
        elif not found:
            continue
        # **Item 130: a sequence item belongs to the key that opened the block.**
        # These rows carry no colon, so they fell through the `if not sep`
        # guard below and were dropped on the floor while the key itself stored
        # the empty string - which `suite.quarantined` reads as "nobody declared
        # one", substituting the WORKSPACE list. A component lost its own
        # declaration AND inherited one nobody accepted for it, in one step.
        elif pending and lead >= pending_depth and _is_item(body):
            found[-1].setdefault(pending, []).append(_scalar(body[1:].strip()))
            continue
        pending, pending_depth = "", -1
        if body.rstrip() == "vcs:":
            in_vcs, vcs_depth = True, lead
            continue
        key, sep, value = body.partition(":")
        if not sep:
            continue
        if (not in_vcs and key.strip() == "vcs"
                and not re.fullmatch(r"(\{\s*\})?", value.strip())):
            raise ProfileError(_inline_vcs(f"components entry {len(found)}", value))
        key, value = key.strip(), _text(value)
        if not in_vcs and key == "name":
            found[-1]["name"] = value
        elif in_vcs and key in VCS_KEYS:
            found[-1]["vcs"][key] = value in ("true", "True") if key == "deny_raw_git" else value
        elif not in_vcs and key in COMPONENT_KEYS:
            if not value.strip() and key in COMPONENT_LIST_KEYS:
                # A bare `key:` opens a sequence and the key is NOT set until an
                # item arrives. **A block with no items is None, not `[]`** -
                # `profile_list` settled that one level up, and an existing test
                # caught this reader getting it wrong: pre-creating the empty
                # list turned "nobody declared one" into "declared and empty",
                # which killed `quarantined`'s fall-back to the workspace list.
                pending, pending_depth = key, lead
                continue
            found[-1][key] = value
    return found


def _one_component(target: Path, lines: list[str]) -> list[dict]:
    """The single-repository case of `components`: one component, synthesised."""
    # T128, the owner's ruling. ONE component is synthesised - the degenerate
    # single-repository case - only for a profile this reader could read: at least
    # one top-level key, and none of them `components`. A profile with no key it
    # can read (a top-level list, a file that is not YAML, an empty one), or a
    # `components:` block holding nothing it recognises, is REFUSED by name: read
    # as one component, it made `worktree.overlap()`, `deploy`, `release` and
    # `suite` answer about a single repository for an estate nothing had read.
    # A PyYAML failure alone stays a printed limit (the 2026-09-01 ruling), and
    # NO profile is not an unreadable one - it is the repository nobody installed
    # into, which every caller's default depends on.
    name, text, _ = _profile_source(target)
    if text is not None:
        keys = profile_blocks(lines)
        if "components" in keys:
            raise ProfileError(
                f"{name} declares components: but holds none this reader can read - "
                f"each entry starts `- name: <component>`. For a single repository, "
                f"remove the key; the repository is then its own one component")
        if not keys:
            raise ProfileError(
                f"{name} has no top-level key this reader can read ("
                + ("it is empty" if not text.strip() else
                   "a top-level list, or not YAML at all")
                + ") - so it cannot say whether this workspace is one repository or "
                  "many, and it is refused rather than read as one")
    return [{"name": Path(target).name, "path": ".", "role": "authoritative",
             "vcs": top_vcs(target, lines), "synthesised": True}]


# ── item 105: pluggable per-phase agents — the binding ───────────────────────
#
# The socket is the process boundary (`RUNNER_CONTRACT.md` §1) and it already
# existed; gates judge whatever a runner leaves and they are untouched. This
# section is the BINDING: who runs a phase, declared in the profile, floored in
# the constitution, refused by name at the moment a phase is entered — never
# discovered mid-phase. Nothing here can weaken a gate: routing decides who
# does the work, gates decide whether the work stands.

#: What a runner declaration may carry. `name` and `command` are required;
#: `capabilities` is required because it is DECLARED, never inferred — the
#: harness cannot tell a command with a model in it from one without (item
#: 29's recorded rule, and the reason `runner_tier` works the same way).
RUNNER_KEYS = ("name", "command", "capabilities", "phases", "note")

RUNNER_CAPABILITIES = ("chat", "tools")

#: Phases whose OWED ARTIFACTS cannot be produced by writing prose alone —
#: code edits across a worktree, suites actually run, review sessions recorded
#: through CLIs, PRs opened, deploys executed, probes asked. A `chat` runner
#: (item 104's endpoint) can write and cannot act, so routing one here is
#: refused BY NAME before the phase starts. The document phases — intake,
#: requirements, design, scoping, triage, closeout — stay routable to an
#: endpoint, which is the enterprise case item 105 exists for. Deliberate and
#: pinned by a test; widen it with a reason, not by accident.
ACT_PHASES = ("implementation", "test", "review", "release", "deploy", "verify")


class RunnerError(RuntimeError):
    """The routing is declared and cannot be honoured. Raised at entry to the
    phase, so the refusal lands before any work is done under the wrong agent
    — a misroute discovered mid-phase is a half-done phase nobody owns."""


def runners_declared(target: Path, lines: list[str] | None = None) -> list[dict]:
    """Every runner the profile declares, from the `runners:` block.

    The same list-entry shape `components:` uses, read the same way, because
    the profile's one existing two-level block is the idiom an adopter has
    already seen - an entry is a sequence item at the block's own indent,
    whichever key it writes first (T469: only `- name:` opened one, so a runner
    written `- command:` first lost its command and renamed the runner before
    it). A declaration with no name is skipped - there is nothing to route to
    it by - where a nameless component is refused, because a component is part
    of what the estate is.
    """
    return [r for r in _runner_entries(_profile_block(target, "runners", lines))
            if r.get("name")]


def _runner_entries(block: list[str]) -> list[dict]:
    """Every entry of the `runners:` block as read, a nameless one included -
    split from `runners_declared` (T494) so the crosscheck compares what this
    reader read, not what `runners_declared` then chose to keep."""
    found: list[dict] = []
    item_lead = -1
    pending, pending_depth = "", -1
    for line in block:
        body = line.strip()
        lead = len(line) - len(line.lstrip())
        if _is_item(body) and item_lead in (-1, lead):
            item_lead = lead
            found.append({})
            pending, pending_depth = "", -1
            rest = body[1:]
            lead += 1 + len(rest) - len(rest.lstrip())
            body = rest.strip()
            if not body:
                continue
        elif not found:
            continue
        # T480: a sequence item belongs to the key that opened it, as in
        # `components()`. A bare `phases:` stored an empty list and its `- review`
        # rows - no colon - were skipped, and `runner_for` reads an empty scope as
        # UNSCOPED: the self-scope bound went unenforced for exactly the block
        # spelling of a list its inline spelling enforced.
        elif pending and lead >= pending_depth and _is_item(body):
            if (item := _scalar(body[1:].strip())) is not None:
                found[-1][pending].append(item)
            continue
        pending, pending_depth = "", -1
        key, sep, value = body.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "phases" and not value:
            found[-1][key] = []
            pending, pending_depth = key, lead
        elif key in RUNNER_KEYS:
            found[-1][key] = (_inline_list(value) if key == "phases"
                              else _text(value))
    return found


def phase_runner_map(target: Path, lines: list[str] | None = None) -> dict[str, str]:
    """`{phase word: runner name}` from the profile's `phase_runners:` section.

    Phase words, not P-numbers, because that is the vocabulary the profile
    already speaks (`phases:` lists words). An unmapped phase keeps today's
    behaviour: whoever is driving drives.
    """
    out: dict[str, str] = {}
    for line in _profile_block(target, "phase_runners", lines):
        key, sep, value = line.strip().partition(":")
        if sep and key.strip() and value.strip():
            out[key.strip()] = _text(value.strip())
    return out


def runner_for(target: Path, phase: str) -> dict | None:
    """Who runs `phase` (a word, e.g. "implementation"), or None when unmapped.

    **Every refusal is by name and lands at phase entry.** In order: a routed
    name nobody declared; a capability that is not one (declared, never
    inferred — an unknown capability is a control that cannot be evaluated,
    the `self_approval` precedent); a `chat` runner on a phase that must ACT;
    a runner routed outside its own declared `phases:` scope; a runner outside
    the org's `allowed_runners` floor; and — only under `require_conformance`
    — a runner with no green provider-smoke row, because the smoke is the
    conformance evidence (it exists and gains its consumer here).

    The conformance dial is read from the constitution and from nowhere else.
    An override parameter existed here briefly and no caller ever passed it -
    an untested branch on the guard every phase entry goes through, where a
    future caller could have bypassed the constitution silently.
    """
    # One read of the profile for both block readers: `phase_runner_map` and
    # `runners_declared` each called `_profile_lines` independently, so a
    # single resolve parsed the same file twice - and `drive` resolves each
    # phase about three times per walk.
    lines = _profile_lines(target)
    name = phase_runner_map(target, lines).get(phase)
    if name is None:
        return None
    declared = {r["name"]: r for r in runners_declared(target, lines)}
    if name not in declared:
        raise RunnerError(
            f"phase_runners routes {phase} to {name!r} and no runner of that "
            f"name is declared - the runners: block knows "
            f"{', '.join(sorted(declared)) or 'none at all'}")
    runner = declared[name]
    caps = str(runner.get("capabilities", "")).strip()
    if caps not in RUNNER_CAPABILITIES:
        raise RunnerError(
            f"runner {name!r} declares capabilities {caps or '(nothing)'!r} - "
            f"one of {', '.join(RUNNER_CAPABILITIES)}. Capabilities are "
            f"declared, never inferred, and one that cannot be read is a "
            f"control that cannot be evaluated")
    if caps == "chat" and phase in ACT_PHASES:
        raise RunnerError(
            f"{phase} must ACT - its artifacts cannot be produced by writing "
            f"prose alone - and {name!r} declares `chat`: it writes, it cannot "
            f"act. Route a tool-capable runner, or leave the phase unmapped")
    scope = runner.get("phases") or []
    if scope and phase not in scope:
        raise RunnerError(
            f"runner {name!r} declares itself for {', '.join(scope)} only, and "
            f"{phase} is not among them - its self-scope is a bound, not a hint")
    doc = load(Path(target))
    allowed = doc.get("allowed_runners") or []
    if allowed and name not in allowed:
        raise RunnerError(
            f"the constitution's allowed_runners floor names "
            f"{', '.join(allowed)} and {name!r} is not among them - the org "
            f"pins the set, a profile picks within it")
    strict = doc.get("require_conformance")
    if strict in (True, "true", "True"):
        # Imported BY STRING, lazily, because this module is embedded in the
        # emitted ledger check, which may reach nothing outside tools.sdlc
        # (test_no_embedded_module_reaches_outside_tools_sdlc). The emitted
        # check answers merge questions and never routes a runner, so this
        # branch is unreached there - and if it ever were, the failure below
        # is a NAMED refusal, not an ImportError: a control that cannot be
        # evaluated has not been satisfied, the self_approval precedent. The
        # guard knows this import through its spelled-out exemption table.
        import importlib
        try:
            smoke = importlib.import_module("tools.provider_smoke")
        except ImportError:
            raise RunnerError(
                "require_conformance is on and the conformance evidence "
                "cannot be read from here - this environment has no "
                "provider_smoke module, so the control cannot be evaluated "
                "and is therefore not satisfied") from None
        rows, _ = smoke.read_record(Path(target))
        # BOTH sides through the same sanitiser. `record()` stores the runner
        # name through `safe()` (scrub + inline mask), so comparing a stored
        # row against the profile's VERBATIM command made a genuinely green
        # runner match nothing the moment its command carried anything the
        # scrubber rewrites - refusing phase entry by name for a runner that
        # had passed. An equality that gates phase entry may not have two
        # different sanitisations on its two sides - so the secrets come from
        # the same producer and through the same rule `record()` uses, and this
        # side was wrong twice. `dict(os.environ)` cannot see a key the harness
        # `.env` supplies (the very key RUNNER_CONTRACT §3 tells adopters to put
        # there), and `secret_values`' length floor cannot see a short one; on
        # either miss the stored name is redacted, this one is not, and a
        # genuinely green runner is refused phase entry by name.
        want = smoke.safe(str(runner.get("command", "")),
                          smoke.named_secret_values(smoke.runner_env()))
        greens = [r for r in rows if r.get("runner") == want
                  and r.get("verdict") == "green"]
        if not greens:
            raise RunnerError(
                f"require_conformance is on and {name!r} has no green "
                f"provider-smoke row for its command - run provider_smoke.py "
                f"--runner against it once, or turn the dial off")
    return runner


def top_vcs(target: Path, lines: list[str] | None = None) -> dict:
    """The profile's top-level `vcs:` block.

    `lines` is injectable so the single-repository case in `components` can
    hand over the profile it has already read, rather than opening the same
    file a second time to build the synthesised component's vcs block.
    """
    out: dict = {}
    for line in _profile_block(target, "vcs", lines):
        key, sep, value = line.strip().partition(":")
        if sep and key.strip() in VCS_KEYS:
            key, value = key.strip(), _text(value)
            out[key] = value in ("true", "True") if key == "deny_raw_git" else value
    return out


#: The `project:` block's keys (T442), which `sdlc-init.py` renders from the
#: pre-INIT `[project]` metadata. Read BY BLOCK, never via `profile_find`: that
#: answers "at any depth, first match", and `name`/`owner`/`description` are
#: exactly the spellings some other block will one day carry.
PROJECT_KEYS = ("name", "description", "owner", "criticality", "default_branch",
                "issue_tracker", "compliance")


def profile_project(target: Path, lines: list[str] | None = None) -> dict:
    """The profile's top-level `project:` block, or {} when it declares none
    (an install from before T442). `compliance` is a list; the rest strings."""
    out: dict = {}
    for line in _profile_block(target, "project", lines):
        key, sep, value = line.strip().partition(":")
        key = key.strip()
        if not sep or key not in PROJECT_KEYS:
            continue
        value = value.strip()
        if key == "compliance":
            inner = value[1:-1] if value.startswith("[") and value.endswith("]") else ""
            out[key] = [_unquote(v) for v in inner.split(",") if v.strip()]
        elif value.startswith('"'):
            # The installer writes these double-quoted with `\\` and `\"`
            # escaped (a description is free text); undo exactly those two.
            out[key] = re.sub(r'\\(["\\])', r"\1", _unquote(value))
        else:
            out[key] = _unquote(value)
    return out


def component(target: Path, name: str) -> dict:
    """One component by name, refusing rather than guessing."""
    every = components(target)
    for c in every:
        if c["name"] == name:
            return c
    raise PolicyError(f"no component named {name!r} - one of "
                      + ", ".join(repr(c["name"]) for c in every))


def vcs_problems(policy: dict, comp: dict) -> list[str]:
    """Why this component's `vcs` block cannot be used. Checked where it is
    DECLARED, so an operator is sent to the file rather than meeting it as a
    push that will not go."""
    vcs = comp.get("vcs") or {}
    problems = [f"component {comp['name']}: {p}" for p in
                pattern_problems(policy, branch_pattern(policy, vcs))]
    remote = str(vcs.get("push_remote", "") or "").strip()
    allowed = push_remotes(policy)
    if remote and remote not in allowed:
        problems.append(f"component {comp['name']}: push_remote {remote!r} is not one "
                        f"the policy allows ({', '.join(allowed)})")
    if (role := comp.get("role", "authoritative")) not in COMPONENT_ROLES:
        problems.append(f"component {comp['name']}: role {role!r} is not one of "
                        f"{', '.join(COMPONENT_ROLES)}")
    return problems


def gate_overrides(target: Path,
                   lines: list[str] | None = None) -> dict[str, str]:
    """The profile's per-gate overrides, from a `gates:` block of `G<n>: state`."""
    found: dict[str, str] = {}
    for line in _profile_block(target, "gates", lines):
        stripped = line.strip()
        if ":" in stripped:
            gate, _, state = stripped.partition(":")
            found[gate.strip()] = state.strip()
    return found


def regime(policy: dict, profile: dict) -> dict:
    """The preset a run lands on, before any override is applied."""
    chosen = profile.get("gate_profile")
    name = chosen if chosen in PRESETS else policy["gate_profile_floor"]
    return {"name": name, **{k: list(v) if isinstance(v, tuple) else v
                             for k, v in PRESETS[name].items()}}


def hard_gates(policy: dict, profile: dict, overrides: dict | None = None,
               standing: set[str] | None = None) -> list[str]:
    """Which gates halt this run. A preset's set, plus anything an override made
    hard — never minus anything, which `tighten_only` is what refuses.

    **`standing` is T441c's orphan filter.** This function iterates the literal
    regime `GATES`, so on its own it can name a gate that halts nothing because
    the pipeline dropped its phase — G1 under `team` on a workspace with no
    `graph`. It cannot read the pipeline itself (`policy` is imported BY `stage`,
    so `policy`→`stage` is a cycle), so a caller that HAS resolved the pipeline
    passes the set of gate ids that actually stand and the result is filtered to
    those. Omitted, the behaviour is unchanged — the pure-policy callers (the
    `--show`/`--validate` door, `effective`) keep the regime set, and
    `stage.resolved_hard_gates` is the full reconciled accessor for everyone who
    has a target."""
    base = set(regime(policy, profile)["hard_gates"])
    for gate, state in (overrides or {}).items():
        if gate in GATES and state == "hard":
            base.add(gate)
    return [g for g in GATES if g in base
            and (standing is None or g in standing)]


def tighten_only(policy: dict, profile: dict, components: int = 0,
                 overrides: dict | None = None, phases: list[str] | None = None,
                 gates: list[str] | None = None) -> list[str]:
    """Every dial the run configuration tries to LOOSEN, named with the file
    that would have to change instead.

    This is the item's centre. A run may always be stricter than the
    constitution; it may never be laxer, and finding out at merge time that a
    profile quietly disabled the review gate is exactly the discovery this
    prevents.

    **`phases` and `gates` are T441c's resolved floors input.** The
    `required_phases` / `required_gates` floors compare against what the profile
    actually RESOLVES to — its active phase names, and its resolved HARD gate ids
    — which this module cannot compute (it is imported BY `stage`, so
    `policy`->`stage` is a cycle). The caller that has resolved the pipeline
    (`approve.record`) passes them; a caller that cannot (the pure-policy
    `--validate`/`--show` door) leaves them None and those two floors are skipped
    rather than guessed, because a floor checked against a set nobody resolved is
    a control someone only thinks they have. Enforcement still lands where a run
    is actually taken.
    """
    problems = []
    where = "policy.yml"
    preset = regime(policy, profile)

    chosen = profile.get("gate_profile")
    if chosen in GATE_PROFILES:
        floor = policy["gate_profile_floor"]
        if GATE_PROFILES.index(chosen) < GATE_PROFILES.index(floor):
            problems.append(f"gate_profile: {chosen!r} is looser than the floor "
                            f"{floor!r} - raise it, or change {where}")

    # The effective floor for the remaining dials is the STRICTER of the
    # constitution and the regime the profile itself selected. Comparing only
    # against policy.yml let `gate_profile: regulated` sit beside `signing: none`
    # and `block_on: [critical, high]` - a run declaring a regime and then opting
    # out of what the regime means, which is the same hole one level up.
    signing = profile.get("signing")
    if signing in SIGNING_MODES:
        floor = max((policy["signing_floor"], preset["signing"]),
                    key=SIGNING_MODES.index)
        if SIGNING_MODES.index(signing) < SIGNING_MODES.index(floor):
            source = ("policy.yml" if floor == policy["signing_floor"]
                      else f"gate_profile: {preset['name']}")
            problems.append(f"signing: {signing!r} is looser than {floor!r}, which "
                            f"{source} requires")

    # Item 38's dial. Same shape as `signing`, and same reason: a profile that
    # drops changed-file coverage back to advisory has turned a control off
    # without anyone deciding to remove it.
    coverage = profile.get("coverage_enforcement")
    if coverage in ENFORCEMENT_MODES:
        floor = policy["coverage_enforcement_floor"]
        if ENFORCEMENT_MODES.index(coverage) < ENFORCEMENT_MODES.index(floor):
            problems.append(f"coverage_enforcement: {coverage!r} is looser than the "
                            f"floor {floor!r} - raise it, or change {where}")

    block_on = profile.get("block_on")
    if isinstance(block_on, list):
        required = set(policy["block_on_floor"]) | set(preset["block_on"])
        missing = [s for s in sorted(required, key=SEVERITIES.index, reverse=True)
                   if s not in block_on]
        if missing:
            source = (where if set(missing) <= set(policy["block_on_floor"])
                      else f"{where} / gate_profile: {preset['name']}")
            problems.append(f"review.block_on drops {', '.join(missing)}, which "
                            f"{source} requires - a severity removed here is a review "
                            f"loop that exits with those findings open")

    # T441c: the phase and gate floors. Tighten-only — the org sets a minimum in
    # policy.yml and a project may only ADD to it. Each is checked against the
    # RESOLVED set the caller passed; None means the caller could not resolve the
    # pipeline (the cycle), so the floor is left unchecked here.
    required_phases = policy.get("required_phases") or []
    if required_phases and phases is not None:
        missing = [p for p in required_phases if p not in phases]
        if missing:
            them = "them" if len(missing) > 1 else "it"
            problems.append(
                f"required_phases: {where} requires {', '.join(missing)} in "
                f"phases[], which this profile does not run - list {them} in the "
                f"profile's phases[] with the capability it needs (`tools/sdlc/stage.py "
                f"--list` says which), or drop the requirement in {where}")
    required_gates = policy.get("required_gates") or []
    if required_gates and gates is not None:
        missing = [g for g in required_gates if g not in gates]
        if missing:
            them = "them" if len(missing) > 1 else "it"
            problems.append(
                f"required_gates: {where} requires {', '.join(missing)} to be a "
                f"hard stop, which this run does not stand - add the step to "
                f"phases[] and extra_gates, or set gates: {{{missing[0]}: hard}} "
                f"for a builtin the regime left advisory, or drop the requirement "
                f"in {where} (add {them} back)")

    # STRUCTURAL, and deliberately not a policy dial: `solo` above one component
    # cannot be selected at all. A single reviewer signing off changes that span
    # repositories is the regime failing to describe what is happening, and no
    # setting in policy.yml may permit it - which is what makes this guard worth
    # more than a floor it could be raised past.
    # Read the RESOLVED regime, not the declared one: an absent or misspelt key
    # falls back to `gate_profile_floor`, whose shipped default is `solo`, so
    # asking `chosen` let DELETING the line buy exactly what writing it is refused.
    if preset["name"] == "solo" and components > 1:
        problems.append(f"gate_profile: solo is unselectable with {components} "
                        f"components - one reviewer cannot sign off changes that span "
                        f"repositories; use team or regulated (no setting in {where} "
                        f"permits it, which is the point)")

    # Per-gate overrides may only make a gate harder.
    preset = regime(policy, profile)
    for gate, state in sorted((overrides or {}).items()):
        if gate not in GATES:
            problems.append(f"gates.{gate}: unknown gate - one of {', '.join(GATES)}")
        elif state not in GATE_STATES:
            problems.append(f"gates.{gate}: {state!r} - one of "
                            f"{', '.join(GATE_STATES)}")
        elif state == "advisory" and gate in preset["hard_gates"]:
            extra = (" - and it is the merge gate, which the CI ledger check reads; "
                     "no regime makes it advisory" if gate == MERGE_GATE else "")
            problems.append(f"gates.{gate}: advisory, but {preset['name']} makes it "
                            f"hard - a run may add a gate, never remove one{extra}")
        elif state == "advisory" and gate == MERGE_GATE:
            problems.append(f"gates.{MERGE_GATE}: advisory - the merge gate is hard in "
                            f"every regime; the halt rule is not a dial")
    return problems


def effective(policy: dict, profile: dict, overrides: dict | None = None) -> dict:
    """What the run actually runs under, once the preset and the floor are applied.

    Only meaningful when `tighten_only` is empty; a loosening attempt is refused,
    never clamped, because silently raising someone's setting hides that they
    asked for something the constitution forbids.
    """
    preset = regime(policy, profile)
    block = set(profile.get("block_on") or []) | set(preset["block_on"])
    declared = profile.get("signing")
    return {
        "gate_profile": preset["name"],
        # The strictest of what was asked for, what the regime means, and what the
        # constitution requires. Taking the profile's word here would let a
        # declared `none` undo the regime the same profile selected.
        "signing": max([m for m in (declared, preset["signing"],
                                    policy["signing_floor"]) if m in SIGNING_MODES],
                       key=SIGNING_MODES.index),
        "block_on": sorted(block | set(policy["block_on_floor"]),
                           key=SEVERITIES.index, reverse=True),
        "hard_gates": hard_gates(policy, profile, overrides),
        "g3_granularity": preset["g3_granularity"],
        "self_approval": policy["self_approval"],
    }


def _indent_problem(lines: list[str], name: str) -> str:
    """The first line whose leading whitespace is not made of spaces.

    **The character rule, and it is the cheap half of item 117.** A leading
    whitespace RUN must be ordinary spaces: a tab is refused because `parse`
    already refuses one for `policy.yml` and PyYAML raises on one, and anything
    else that `str.isspace` accepts is refused NAMING ITS CODEPOINT, because
    "your indentation is wrong" about a character nobody can see is a refusal
    nobody can act on.

    Measured before it was written: the shipped template carries zero tabs and
    zero non-space indent characters, and so does every profile the installer
    renders — so agreeing with PyYAML here costs nothing that anybody writes on
    purpose.

    Comment-stripped lines, so a `#` note indented with ORDINARY whitespace is
    not a finding: its indentation means nothing to any reader, and refusing one
    would make the check useless in exactly the file people annotate.

    **An INVISIBLE-space indent before a `#` still refuses, and that is
    deliberate.** This paragraph used to say "indented with anything at all",
    which item 117 had already measured to be wrong and this docstring was not
    updated to match: `_strip_comment` treats `#` as a comment only when a
    space or a tab precedes it, so a note indented with U+00A0 survives into
    this check and is refused - and **PyYAML raises on the same bytes**
    (`while scanning a simple key`). Refusing is therefore AGREEING with the
    reference reader; passing would be this reader inventing a third opinion
    about the one character the whole item is about.

    A max-effort review reported the refusal as a defect on the strength of the
    sentence above, which is what a stale docstring costs: the prose was the
    thing that was wrong, and "fixing" the behaviour would have overturned a
    ruling somebody had already measured.
    """
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        lead = line[:len(line) - len(line.lstrip())]
        if "\t" in lead:
            return tab_refusal(name, n)
        for ch in lead:
            if ch != " ":
                return (f"{name}:{n}: indented with U+{ord(ch):04X} "
                        f"({unicodedata.name(ch, 'an unnamed character')}), which "
                        f"looks like a space and is not - use ordinary spaces")
    return ""


def _duplicate_key_problem(lines: list[str], name: str) -> str:
    """The first TOP-LEVEL key set twice, named by line, or "".

    Item 224. `parse` refuses a duplicate key for `policy.yml` and the profile
    reader did not, so the hand reader took the FIRST and PyYAML the LAST and the
    two enforced different values silently - `gate_profile: solo` above
    `gate_profile: regulated` reads `solo` here and `regulated` there, a
    loosening. PyYAML does NOT raise on a duplicate; it keeps the last. So this
    is the reader being deliberately stricter than PyYAML for the one shape where
    "agree with PyYAML" would mean agreeing to decide by POSITION which of two
    values is the policy - and it holds with PyYAML absent, which the value
    crosscheck could not.

    **All top-level keys, with NO exemption for the sequence blocks.** Two
    `components:` (or `runners:`) is the same disagreement one level up: the
    profile reader MERGES them while PyYAML keeps the last, so exempting them
    would leave the exact hand-vs-PyYAML split this refusal exists to close. A
    key repeated INSIDE a component - each component's own `test_command` - is
    not a top-level key: `_names_block` names only a header line and answers `""`
    for an indented or `- ` row, so nested repeats never reach this. The refusal
    text is `parse`'s own, through `duplicate_key_refusal`, so the two readers
    cannot drift apart about what a duplicate key says.
    """
    seen: set[str] = set()
    for index, _block, line in _profile_rows(lines):
        key = _names_block(line)
        if not key:
            continue
        if key in seen:
            return duplicate_key_refusal(name, index + 1, key)
        seen.add(key)
    return ""


def _unclosed_list_problem(lines: list[str], name: str) -> str:
    """The first inline list opened with `[` and never closed with `]`, or "".

    `phases: [intake, implementation, release` was read as None - ABSENT - by
    `profile_list`, so `stage.py --list` took the solo default and reported a
    phase the file lists as *not listed*. INVALID is not ABSENT (item 117): this
    is the harness's OWN reader, so it refuses a list it cannot close rather than
    reading the key as unset. PyYAML raises on the same bytes, so refusing here
    AGREES with the reference reader.

    The condition is the EXACT complement of how every reader in this module
    decides a value IS an inline list - `startswith("[")` and `endswith("]")` -
    so a closed list, an empty `[]`, a block list (the value after the colon is
    empty) and a quoted scalar that merely contains a bracket are all left to
    read. A value nested two levels deep or a bracketed block-sequence ITEM
    carries no `key:` on its line and so is left to the crosscheck, which is
    where PyYAML's own refusal already lands.
    """
    for index, _block, line in _profile_rows(lines):
        _key, sep, rest = line.strip().partition(":")
        if not sep:
            continue
        value = rest.strip()
        if value.startswith("[") and not value.endswith("]"):
            return (f"{name}:{index + 1}: inline list {value!r} is opened with "
                    f"'[' and never closed with ']' - this reader refuses a list "
                    f"it cannot close rather than reading the key as unset")
    return ""


#: The four value NODES a scalar/line reader cannot represent — YAML that names
#: a value indirectly (`&`/`*`), retypes it (`!`) or opens a flow mapping (`{`).
#: A flow SEQUENCE (`[`) is deliberately absent: `_inline_list` reads it, so it
#: is a shape this reader DOES support and must not refuse.
_UNREADABLE_VALUE_PREFIX = {"&": "a YAML anchor", "*": "a YAML alias",
                            "!": "a YAML tag", "{": "a YAML flow mapping"}

#: The one sentence every branch of `_unreadable_shape_problem` ends on, so the
#: refusal names what the reader CAN read as well as what it will not guess at.
_READER_SHAPES = ("this reader reads only `key: scalar`, `key: [inline list]`, a "
                  "block list of `- scalar`, and one level of `subkey: scalar` - "
                  "it refuses a shape it cannot read faithfully rather than "
                  "enforcing a value nobody wrote")


def _looks_like_mapping(body: str) -> bool:
    """Whether a block-sequence ITEM is itself a MAPPING rather than a scalar.

    YAML makes an item a mapping only where a `:` is followed by whitespace or
    ends the node, OUTSIDE quotes and flow brackets: `- rule: a` is a one-key
    map, while `- tests/x.py::test_y` and `- a:b` are plain scalars because no
    colon there is followed by a space. This reader collects a `- ` item as one
    scalar and drops any continuation line, so `- rule: a` / `  why: b` becomes
    the string `'rule: a'` with `why: b` gone — a value nobody wrote, then
    enforced. A quoted item is a scalar whatever it holds, so a leading quote is
    never a mapping. The rule is the one `_split_items` and `_scalar` already
    live by: know just enough about quotes and brackets, and no more.
    """
    text = body.strip()
    if not text or text[:1] in ("\"", "'"):
        return False
    quote = ""
    depth = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("\"", "'"):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            following = text[i + 1:i + 2]
            if following == "" or following.isspace():
                return True
    return False


def _unreadable_shape_problem(lines: list[str], name: str) -> str:
    """The first value whose SHAPE this reader would misread, named, or "".

    **Item 117's altitude, reached from the value side.** The hand reader
    enumerates the value SHAPES it understands, and a construct the real parser
    reads as non-scalar — a list of mappings, a nested list, an anchor, an alias,
    a tag, a flow mapping — matches no branch, so the reader hands back an empty
    result (`profile_list` returns `['rule: a']` for a suppression written as
    `- rule: a` / `  why: b`, dropping `why` and ENFORCING the string) or the
    literal YAML sigil as a bare scalar (`gate_profile: &g regulated` read as
    the string `'&g regulated'`). Neither refuses, so the adopter's file silently
    does something other than what it says — the exact failure `parse` refuses
    for `policy.yml`, which this brings to the profile.

    **Pure stdlib, and that is the whole point.** It runs in `_profile_lines`
    alongside the character, duplicate-key and unclosed-list rules and BEFORE the
    PyYAML crosscheck, so it holds on the zero-dependency machine the reader
    exists for. The crosscheck catches the anchor/alias/tag class only where
    PyYAML is installed (a scalar mismatch) and never catches the list-of-maps,
    nested-list or flow-mapping class at all — a non-scalar value matches neither
    of its scalar/list-of-scalars branches — so the refusal has to live here.

    **`components:` and `runners:` are skipped, because their list-of-mappings is
    the ONE such shape this harness reads** — through `components()`, its own
    reader, not this flat one. Refusing it would refuse the shipped multi-component
    profile, which is item 116's regression measured rather than risked. Every
    other block is flat, so a list of mappings under it is unreadable by design.
    """
    for index, block, line in _profile_rows(lines):
        if block in SEQUENCE_BLOCKS:
            continue                     # a list of maps here has its own reader
        at = index + 1
        stripped = line.strip()
        if stripped == "-" or stripped.startswith("- "):
            if not block:
                # A `- ` at column 0 with no key open above it names NO block and
                # the reader IGNORES it - `parse`'s "a list at the top level has
                # no key", kept as the tolerated shape it already is rather than a
                # new refusal (see test_a_top_level_sequence_entry_names_no_block).
                continue
            item = stripped[1:].strip()
            if item == "-" or item.startswith("- "):
                return (f"{name}:{at}: a nested list under {block!r} - "
                        f"{_READER_SHAPES}")
            if _looks_like_mapping(item):
                return (f"{name}:{at}: a list of mappings under {block!r} "
                        f"({stripped!r}) - {_READER_SHAPES}")
            if shape := _UNREADABLE_VALUE_PREFIX.get(item[:1]):
                return (f"{name}:{at}: {shape} under {block!r} ({item!r}) - "
                        f"{_READER_SHAPES}")
            continue
        key, sep, rest = stripped.partition(":")
        if not sep:
            continue
        value = rest.strip()
        if shape := _UNREADABLE_VALUE_PREFIX.get(value[:1]):
            return (f"{name}:{at}: {key.strip()!r} is {shape} ({value!r}) - "
                    f"{_READER_SHAPES}")
    return ""


#: T371a: profile text a caller is ASKING ABOUT, keyed by resolved target, in
#: place of the file on disk. Empty outside `proposed_profile`.
_PROPOSED: dict[str, str] = {}


@contextlib.contextmanager
def proposed_profile(target: Path, text: str):
    """Inside this block, every reader of `target`'s profile reads `text`.

    **How `sdlc config` asks "would a run accept this?" before writing it.**
    The wizard's candidate goes through the very door a run reads through, so
    it meets the same refusals and resolves to the same pipeline the file
    would. Nothing else answers that honestly. A copy of the target in a scratch
    directory drifts the first time a resolver reads one more file, and asking
    after the write is the write-then-refuse order that T465 files as a defect.

    Keyed by the RESOLVED target, so `.` and its absolute spelling are one
    proposal, and a proposal for one repository never answers for another.
    It is restored on the way out, whether or not the block raised."""
    key = str(Path(target).resolve())
    before = _PROPOSED.get(key)
    _PROPOSED[key] = text
    try:
        yield
    finally:
        if before is None:
            _PROPOSED.pop(key, None)
        else:
            _PROPOSED[key] = before


def _profile_source(target: Path) -> tuple[str, str | None, list[str]]:
    """`(name, text, comment-stripped lines)` — the ONE function that names the file.

    `text` is None when there is no profile to read, which is not an error: a
    repository nobody has installed into is not a repository with a broken
    profile, and every caller's `[]` depends on that staying true.

    Two functions need this file — the reader and the stated limit — and item
    116's guard is that only ONE may name it, because the two readers that
    opened it for themselves are two of the three that disagreed about what
    indentation is. So the raw text is handed out from here rather than fetched
    again.

    `Path(target)` rather than `target`, because callers reach this module from
    argparse and from other modules and some of them hold a string. A reader
    that raises TypeError on a str is a reader whose caller falls back to a
    default — which is how the CI gate quietly went on using the branch pattern
    this module was written to replace.
    """
    path = Path(target) / ".sdlc" / PROFILE_NAME
    proposed = _PROPOSED.get(str(Path(target).resolve())) if _PROPOSED else None
    if proposed is not None:
        text = proposed
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return path.name, None, []
    return path.name, text, [_strip_comment(line).rstrip()
                             for line in text.splitlines()]


def _profile_lines(target: Path) -> list[str]:
    """The profile, comment-stripped, or [] when there is none to read.

    **This is the one door, which is why the refusal lives here.** Sixty-seven
    call sites across ten shipped modules read this profile, and each one's next
    act is to ENFORCE what it read — so a reader that hands back a plausible
    wrong answer arms every gate downstream with it. Refusing here reaches all
    of them without any of them being changed.

    Two kinds of channel, independent by construction. The STDLIB rules — the
    character rule, the duplicate-key rule (item 224), the unclosed-inline-list
    rule (item 130), and the unreadable-shape rule (a value this scalar/line
    reader would misread — a list of mappings, a nested list, an anchor, an
    alias, a tag, a flow mapping) — are pure stdlib and always run, so they hold
    on a machine with no packages, which is the one the profile reader exists
    for. `profile_crosscheck` is the second opinion: it asks PyYAML, which may
    not be installed, and its own failure to read the file is a STATED LIMIT
    rather than a refusal (`profile_limits`).
    """
    name, text, lines = _profile_source(target)
    if text is None:
        return []
    if problem := _indent_problem(lines, name):
        raise ProfileError(problem)
    if problem := _duplicate_key_problem(lines, name):
        raise ProfileError(problem)
    if problem := _unclosed_list_problem(lines, name):
        raise ProfileError(problem)
    if problem := _unreadable_shape_problem(lines, name):
        raise ProfileError(problem)
    if problem := profile_crosscheck(text, lines, name)[0]:
        raise ProfileError(problem)
    return lines


def _indented(line: str) -> bool:
    """Whether a profile line sits INSIDE the block above it.

    **One rule, in one place, and that is this item.** SEVEN hand-rolled block
    scanners each decided this for themselves and they did not agree: four
    asked `line.lstrip()`, so any whitespace indents; three asked for a space
    or a tab specifically. The characters they disagree about are the
    INVISIBLE SPACES - a non-breaking space is what a profile pasted out of a
    web page, a wiki or a chat client carries - and `splitlines` already eats
    every other candidate (form feed, vertical tab), so NBSP and its Unicode
    siblings are the whole of the set.

    **Measured, on one file, one character, three consequences.**
    `component_count` read 0 components where `components` read 2, so
    `tighten_only` accepted `solo` on a two-component workspace - item 63's
    STRUCTURAL check, the one its own text calls unselectable from any policy.
    `gate_overrides` returned {} and dropped a `G4: hard` override, which is a
    HARDENING lost. And `profile_find` returned a component's `test_command`
    as the workspace's, which is the exact defect its own docstring records
    having been written to fix - so the seventh copy reopened it. Nothing
    anywhere refuses such a file: PyYAML reads it as `{'components': None,
    '  - name': 'b'}` without raising, so these hand readers are the only
    readers there are.

    **The rule is deliberately the conservative one.** All three failures came
    from CLOSING the block early and each one LOST something - a component, a
    hardening, an exactness guarantee. A reader that keeps the block open when
    the indentation is strange errs toward reporting more, which is the
    direction a gate is allowed to be wrong in.
    """
    return line[:1].isspace()


def _profile_sections(lines: list[str]):
    """`(block, line)` for every non-blank profile line.

    `block` names the top-level key the line sits under, and a top-level key's
    own line sits under ITSELF - so the `components:` header belongs to
    `components`, which is what lets `profile_find` skip that block by header
    and body together the way it always has.

    The name is taken by `partition(":")` rather than by asking
    `startswith("components:")`, because the caller should not have to spell
    its own block name into a prefix test.

    **A non-indented line that is not `key:` names NO block and CLOSES the one
    above** — the colon requirement all seven copies enforced and which item
    116 dropped along with their prefix tests. Two things go wrong without it:
    `gates` with no colon opened the `gates` block, so a typo silently became a
    section; and `---`, a legal YAML document marker, named a block that then
    owned every indented line beneath it. `components :` is still the key
    `components`, because PyYAML says it is and a reader that disagreed with
    PyYAML about a space would be inventing a second grammar. A `- ` entry at
    column 0 with NO key open above it names nothing, for the reason `parse`
    gives when it refuses one: a list at the top level has no key. With a key
    open it belongs to that key — see `_profile_rows`, item 221.
    """
    for _index, block, line in _profile_rows(lines):
        yield block, line


def _profile_rows(lines: list[str]):
    """`(index, block, line)` for every non-blank profile line - the walk
    itself, with the line's own position kept.

    **Item 146 needed the position and `_profile_sections` had thrown it
    away.** `learning.render` is the only writer of `.sdlc/profile.yml` and it
    has to know which LINES a declaration occupies in order to replace it; with
    no way to ask, it carried a second locator of its own, and the two
    disagreed about a comment between two items and about a space before the
    colon. Both spellings the readers handle, and the writer destroyed the
    document over each. `_profile_sections` is derived from this rather than
    the other way round, so there is one walk and it cannot drift.

    **A block is a DEPTH, not an indentation - item 221.** This named a line's
    block only from NON-INDENTED lines and a non-indented `- ` CLEARED it, so a
    `components:` or `runners:` sequence written with its rows at column 0 -
    legal YAML, and `_profile_entry_at` below says that spelling *was measured,
    not assumed* - named no block at all. `_profile_entry_at` skips a row only
    when its block is a `SEQUENCE_BLOCK`, so every dial inside such a component
    was promoted to the WORKSPACE (`block_on` promoted is item 35's
    tighten-only floor defeated from inside the document, `phases` promoted is
    item 130's recorded consequence verbatim) while `components()` lost the
    component that declared them and synthesised one instead - both halves of
    the same step, and the declaration both lost and promoted.

    Fixed by depth-tracking rather than by refusing the spelling, and item 117
    settled which: this reader must AGREE with PyYAML about SHAPE and refuse
    only what it CANNOT read, so refusing a document PyYAML accepts would
    invert that rule. `opened` is the top-level key whose own line carried no
    value; a column-0 `- ` row belongs to it, and with nothing open it still
    names nothing.
    """
    block = ""
    opened = ""
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not _indented(line):
            body = line.strip()
            if body == "-" or body.startswith("- "):
                block = opened
            else:
                key, sep, rest = body.partition(":")
                block = key.strip() if sep else ""
                opened = block if (sep and not rest.strip()) else ""
        yield index, block, line


def _names_block(line: str) -> str:
    """The top-level key this LINE declares, or `""` — the HEADER test.

    **Item 221 split this out because "is this line its block's header" and "is
    this line indented" stopped being the same question**, and three call sites
    asked the second one meaning the first. Once a column-0 `- ` row belongs to
    the block above it, that row is un-indented and is NOT a header, so a
    reader excluding headers by `_indented` excluded the component's own first
    line and `components()` synthesised over it.

    A BARE `-` needs no clause of its own: it carries no colon, so the key
    test below already answers `""` for it. `_profile_rows` DOES name that
    shape, and that is not a copy left behind - there the bare `-` SELECTS
    the block open above it rather than being rejected, so both spellings
    have to be written out.
    """
    if _indented(line):
        return ""
    body = line.strip()
    if body.startswith("- "):
        return ""
    key, sep, _rest = body.partition(":")
    return key.strip() if sep else ""


def _profile_block(target: Path, name: str,
                   lines: list[str] | None = None) -> list[str]:
    """Every line INSIDE the top-level `name:` block, comment-stripped.

    The header is excluded by name rather than by indentation: it is the one
    line of its block that DECLARES the block (item 221 — a column-0 `- ` row
    is inside the block and is not indented either).

    `lines` is injectable for the reason `runner_for` already discovered - a
    caller that wants two blocks should read the file once, and the two
    functions that used to read it for themselves are exactly the two that
    disagreed with everyone else about what indentation is.
    """
    rows = _profile_lines(target) if lines is None else lines
    return [line for block, line in _profile_sections(rows)
            if block == name and not _names_block(line)]


def profile_blocks(lines: list[str]) -> list[str]:
    """Every top-level key this reader can see, in the order it sees them.

    **Every non-indented key, NOT the keys that have indented content**, and
    that distinction is the whole of whether the crosscheck below is usable:
    measured on the shipped template, 26 top-level keys and only THREE of them
    (`release`, `review`, `vcs`) carry children, so a "blocks with content"
    reading would report the reader as disagreeing with PyYAML about 23 keys
    for every ordinary profile — a gate that fires on everything, which is a
    gate somebody turns off in a week.
    """
    return [block for block, line in _profile_sections(lines)
            if _names_block(line)]


def profile_crosscheck(text: str, lines: list[str],
                       name: str) -> tuple[str, str]:
    """`(refusal, limit)` — the second, independent statement of this file's shape.

    Mirrors `crosscheck` above, and **has ONE fatal branch where that one has
    two**, which a risk pass on 2026-09-01 decided by measuring rather than by
    analogy. `policy.yml` carries no placeholders and PyYAML parses it, so
    refusing a file PyYAML cannot read is safe there. `sdlc_templates/profile.yml`
    carries EIGHTEEN `@@…@@` placeholders and PyYAML raises on it, so the same
    branch here would refuse the harness's own shipped template outright.

    Worse, it would make the hard stop ENVIRONMENT-DEPENDENT: the refusal fires
    only where PyYAML is installed, so a rendered profile whose command value
    contains `: ` — item 31's own recorded defect, *YAML quotes on the baseline
    command* — would hard-stop on the maintainer's machine and pass on the
    zero-dependency one, which is the exact inverse of the property the stdlib
    reader exists for. So an unreadable file is the LIMIT, printed and never
    fatal, and the owner ruled it that way on 2026-09-01.

    **That costs nothing that matters, because PyYAML PARSES the shapes this
    is for.** An NBSP-indented profile reads as
    `{'components': None, '\\xa0 - name': 'b'}` without raising — so the
    surviving branch, a disagreement about the TOP-LEVEL KEY SET, still catches
    the whole invisible-space class: PyYAML sees `\\xa0test_command` as a key of
    its own where the reader thinks the line sits inside `components`.

    A document PyYAML reads as something other than a mapping is a limit too,
    not a refusal: there is no key set to disagree about, and inventing a
    verdict from its absence is the shape this harness keeps refusing.

    **Memoised on the whole input, one entry, and the number is why.**
    Measured: a PyYAML parse costs 6.7 ms against the reader's own 0.28 ms, and
    `suite.declared` reads the profile seven times for ONE component — so a
    26-component estate paid over a second per call to re-parse a file that had
    not changed. One entry cannot grow, and the hot path is the same file read
    repeatedly, which is exactly what it covers.

    **The key is every argument, and a first cut that left `lines` out of it was
    WRONG** — caught by the mutation pass's baseline check before a single
    mutation ran. In production `lines` is always derived from `text`, so the
    two cannot disagree; but this is a public function whose answer depends on
    both, and the test that proves the crosscheck can FAIL works by handing it a
    reader that sees fewer keys for the same text. A memo keyed on a subset of
    its inputs had silently answered that call from the previous one — which is
    a guard nobody can watch fail, item 115's lesson, arriving through the cache
    rather than through the test.
    """
    global _CROSSCHECK_MEMO
    key = (name, text, tuple(lines))
    if _CROSSCHECK_MEMO is not None and _CROSSCHECK_MEMO[0] == key:
        return _CROSSCHECK_MEMO[1]
    answer = _crosscheck_uncached(text, lines, name)
    _CROSSCHECK_MEMO = (key, answer)
    return answer


#: `(key, (refusal, limit))` for the last profile cross-checked, where the key is
#: every argument. See `profile_crosscheck` for the measurement that earned it
#: and for what a narrower key cost.
_CROSSCHECK_MEMO: tuple[tuple, tuple[str, str]] | None = None


def _same_text(value) -> str:
    """One spelling for a scalar, so `85` and `"85"` are not a disagreement.

    PyYAML types what it reads and this reader does not - it hands back the
    text - so a comparison that did not normalise would report every integer
    and every boolean in the file. Booleans lower-case because that is how YAML
    spells them and how the profile is written.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _value_disagreement(name: str, lines: list[str], reference: dict) -> str:
    """The two readers agree about which keys exist. Do they agree about what
    the keys SAY?

    **Item 130 widened this from key SETS to values, and four confirmed defects
    are why.** The old check compared `set(profile_blocks(lines))` against
    `set(reference)` and stopped - so `gate_profile : solo`, which both readers
    see as the key `gate_profile`, could resolve to `solo` for PyYAML and to
    NOTHING here, and `tighten_only` skips a dial it reads as absent. A
    loosening walked past the constitution and the crosscheck was structurally
    unable to notice, because the key sets agreed.

    **Scalars and lists, at the top level and ONE mapping deep - and, inside a
    sequence of maps, each entry's TEXT.** Re-reading `components:` here would
    be a second implementation of the reader rather than a check on it; the two
    defects that lived in there first (a key dropped after a `vcs:` sub-block, a
    block-form list read as the empty string) are covered by tests. **The
    trigger for widening to sequences was a third disagreement inside one, and
    T494 was it**: every component, runner and nested `vcs:` command kept its
    YAML escapes. So `_entry_disagreement` asks the REAL entry readers what each
    entry's text values say and compares them with PyYAML's, entry by entry -
    a check on this reader, not a second one. PyYAML's parse is already done and
    memoised by the caller, so it adds a comparison and not a read.

    **Item 223: one level of nesting IS compared, because the top-level rule had
    left a twin of its own shape uncovered.** A YAML 1.1 boolean nested under a
    mapping - `review.block_on: [on]`, handed back as `['on']` here and read as
    `[True]` by PyYAML - is exactly what the top-level list comparison refuses at
    column 0, and it was silent one level down. Each nested key is read from the
    SECTION's own lines rather than the whole file, so a key that also appears at
    the top level is not mistaken for its nested namesake. A value nested two
    levels deep, or a sequence of maps, is still left to the tests: reading it
    would be a second reader and not a check.

    **Item 222: it asks the reader the CONSUMERS ask.** This called
    `scalar_from_lines`, which skips indented lines and is therefore TOP-LEVEL
    ONLY, while every consumer reaches a value through `profile_scalar` to
    `profile_find`, which reads at ANY depth. So a nested twin of a top-level
    key was what the harness enforced and what the guard could not see -
    measured at `coverage_threshold` reading 10 here and 90 to PyYAML with
    `profile_limits()` returning `[]`. A guard hardened where somebody was
    looking, and it is this item's own guard: item 130 widened it from key SETS
    to VALUES precisely so two readers could not agree about which keys exist
    while disagreeing about what they say.

    Through `_scalar`, because `_profile_entry` hands back the RAW text: a
    comparison that skipped the coercion would report every quoted command and
    every integer in every profile as a disagreement, and a gate that fires on
    everything is a gate somebody turns off in a week.
    """
    # (label a person reads, key the reader looks up, PyYAML's value, the lines
    # to read it from). A mapping section contributes its nested scalars/lists
    # (item 223), read from the section's OWN lines so a nested key is never
    # confused with a top-level twin - the consumers take the first match at any
    # depth (`profile_find`), so scoping the reader to this block is what keeps
    # the comparison about THIS `block_on` and not another of the same name.
    # Built as one list so the comparison below is written once, not per depth.
    targets: list[tuple[str, str, object, list[str]]] = []
    for key in sorted(set(reference) & set(profile_blocks(lines))):
        theirs = reference[key]
        if isinstance(theirs, dict):
            section = [ln for blk, ln in _profile_sections(lines)
                       if blk == key and not _names_block(ln)]
            targets += [(f"{key}.{nk}", nk, nv, section)
                        for nk, nv in theirs.items()]
        else:
            targets.append((key, key, theirs, lines))

    said: list[str] = []
    for label, key, theirs, reader_lines in targets:
        if isinstance(theirs, (str, int, float, bool)):
            entry = _profile_entry(reader_lines, key)
            mine = None if entry is None else _scalar(entry[0])
            if mine is None:
                said.append(f"{label} has a value to PyYAML "
                            f"({_same_text(theirs)!r}) and none here")
            elif _same_text(mine) != _same_text(theirs):
                said.append(f"{label} reads {_same_text(mine)!r} here and "
                            f"{_same_text(theirs)!r} to PyYAML")
        elif isinstance(theirs, list) and all(
                isinstance(v, (str, int, float, bool)) for v in theirs):
            ours = list_from_lines(reader_lines, key)
            if ours is not None and [str(v) for v in ours] != [
                    _same_text(v) for v in theirs]:
                said.append(f"{label} reads {ours!r} here and {theirs!r} to PyYAML")
    said += _entry_disagreement(lines, reference)
    if not said:
        return ""
    return (f"{name}: the standard-library reader and PyYAML disagree about what "
            f"a key SAYS - {'; '.join(said)}. A value one reader cannot see is a "
            f"setting the other will enforce")


def _entry_disagreement(lines: list[str], reference: dict) -> list[str]:
    """Where an entry of `components:` or `runners:` says one thing here and
    another to PyYAML (T494): its text values, a nested `vcs:` included, as the
    REAL entry readers read them. Only text PyYAML reads as text is compared -
    a number or a boolean is typed there and handed back as written here, which
    `_same_text` exists to forgive at the top level - and only keys the reader
    knows, so an adopter's own extra key is not a disagreement. A known key
    PyYAML reads and this reader never stored is one: a silent drop. An entry
    the reader refuses is left to the refusal it raises where it is read."""
    said: list[str] = []
    try:
        entries = _component_entries(lines)
    except ProfileError:
        entries = []            # refused where the profile's components are read
    readers = (("components", ("name", *COMPONENT_KEYS), entries),
               ("runners", RUNNER_KEYS, _runner_entries(
                   [ln for blk, ln in _profile_sections(lines)
                    if blk == "runners" and not _names_block(ln)])))
    for block, known, ours in readers:
        theirs = reference.get(block)
        if not isinstance(theirs, list):
            continue
        for n, (mine, entry) in enumerate(zip(ours, theirs), 1):
            if not isinstance(entry, dict):
                continue
            pairs = [(k, v, mine.get(k)) for k, v in entry.items() if k in known]
            if block == "components" and isinstance(entry.get("vcs"), dict):
                pairs += [(f"vcs.{k}", v, (mine.get("vcs") or {}).get(k))
                          for k, v in entry["vcs"].items() if k in VCS_KEYS]
            for key, value, read in pairs:
                if not isinstance(value, str):
                    continue
                if read is None:
                    said.append(f"{block} entry {n}'s {key} has a value to PyYAML "
                                f"({value!r}) and none here")
                elif isinstance(read, str) and read != value:
                    said.append(f"{block} entry {n}'s {key} reads {read!r} here "
                                f"and {value!r} to PyYAML")
    return said


def _crosscheck_uncached(text: str, lines: list[str],
                         name: str) -> tuple[str, str]:
    try:
        import yaml  # noqa: PLC0415 - optional by design
    except ImportError:
        return "", (f"{name}: PyYAML is not installed, so this file's shape was "
                    f"read by the standard-library reader alone")
    try:
        reference = yaml.safe_load(text)
    except Exception as exc:                # a file YAML rejects is still read
        why = " ".join(str(exc).split())
        return "", (f"{name}: PyYAML cannot read this file ({why}), so its shape "
                    f"was not cross-checked")
    if not isinstance(reference, dict):
        return "", (f"{name}: PyYAML reads this as {type(reference).__name__}, not "
                    f"a mapping, so its shape was not cross-checked")
    mine, theirs = set(profile_blocks(lines)), set(reference)
    if mine == theirs:
        return _value_disagreement(name, lines, reference), ""
    said = []
    if only_mine := sorted(mine - theirs):
        said.append(f"this reader sees {', '.join(only_mine)} and PyYAML does not")
    if only_theirs := sorted(theirs - mine):
        said.append(f"PyYAML sees {', '.join(only_theirs)} and this reader does not")
    return (f"{name}: the standard-library reader and PyYAML disagree about the "
            f"top-level keys - {'; '.join(said)}. A key one reader cannot see is "
            f"a setting the other will enforce"), ""


def profile_limits(target: Path) -> list[str]:
    """What the profile's shape was NOT cross-checked against. Printed; never fatal.

    `validate.limits`'s idiom, and item 108's design lesson reached from the
    other side: calling this a problem would refuse every profile on every
    machine with no PyYAML, which is the zero-dependency configuration this
    harness promises to work in. Saying nothing would let a reader that read the
    file alone report the same confidence as one that had a second opinion.

    Silent when PyYAML is present AND parsing, because then the crosscheck
    really ran and a disagreement is a refusal rather than a note.
    """
    name, text, lines = _profile_source(target)
    if text is None:
        return []                            # no profile: nothing to cross-check
    limit = profile_crosscheck(text, lines, name)[1]
    return [limit] if limit else []


def profile_top_value(target: Path, key: str) -> str | None:
    """One TOP-LEVEL scalar from the profile — the EIGHTH block reader, folded in.

    This lived in `intake.py` with its own answer to what a top-level key is
    (`line.startswith(f"{key}:")` on the RAW line), which measurement showed
    disagreeing with `profile_find` on the same file: an invisible-space-
    prefixed `scope:` read as None here and as its value there. Item 116's
    derived roster could not see it, because that sweep is scoped to this
    module — so the fix is to move the reader rather than widen a list, and the
    roster now sweeps the module SET.

    Deliberately not YAML, for the reason it always was: PyYAML is optional in
    this harness, so a hard import would make intake refuse to run in exactly
    the stripped environment the zero-dependency gate protects. A nested key
    returns None and the caller says so rather than inventing a value.

    The quote walk is kept verbatim: a quoted scalar ends at its CLOSING quote
    and the installer's own writer escapes inner quotes (`_yaml_dq`), so cutting
    an inline comment first would have kept the quotes and handed P5 a command
    no shell can run.
    """
    return scalar_from_lines(_profile_lines(target), key)


def scalar_from_lines(lines: list[str], key: str) -> str | None:
    """`profile_top_value`'s answer, over lines a caller already holds.

    Factored for the same reason item 129 factored `list_from_lines`: the
    crosscheck below runs INSIDE `_profile_lines`, so it cannot call the public
    reader without recursing into the door it is standing in.
    """
    for line in lines:
        head, sep, rest = line.partition(":")
        if _indented(line) or not sep or head.strip() != key:
            continue
        # Item 130: the VALUE comes from the same partition that matched the
        # key. Slicing by `len(key) + 1` was the other half of the defect - it
        # is right only when the colon sits flush against the name, so a
        # profile spelling `gate_profile : solo` would have handed back
        # ": solo" the moment the match was widened to accept it.
        value = rest.strip()
        if value[:1] in ("\"", "'"):
            quote, out, i, closed = value[0], [], 1, False
            while i < len(value):
                ch = value[i]
                if quote == '"' and ch == "\\" and i + 1 < len(value):
                    out.append(value[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    closed = True
                    break
                out.append(ch)
                i += 1
            if closed:
                return "".join(out)
            return value  # unbalanced quoting: hand back what is there, verbatim
        return value.rstrip()
    return None


def profile_find(target: Path, key: str,
                 lines: list[str] | None = None) -> str | None:
    """The raw text after `key:` in `.sdlc/profile.yml`, at ANY depth.

    `lines` is injectable for the reason `_profile_block` already states - a
    caller wanting both the scalar and the block under one key should read the
    file once, and `profile_list` is now that caller.

    One reader for the profile's flat values, because there were three of them
    scanning the same file for one key each and a fourth was about to be
    written. The limit is stated rather than hidden: two blocks may each hold a
    key of the same name and this takes the FIRST, which is the behaviour the
    readers it replaces already had.

    **The `components:` block is skipped**, and that is not a nicety. Every
    component carries its own `test_command`, `coverage_command`,
    `build_command` and `coverage_threshold`, so "at any depth" meant a
    workspace-level question was answered with the FIRST COMPONENT'S answer —
    and `stage.resolve` builds P0's and P5's exact-value blocks out of this,
    so an estate's prompt stated one component's suite as the workspace's. A
    block whose entire purpose is exactness had a plausible wrong value in it.
    `review.block_on` is nested under a SECTION rather than a list entry and
    is still found, which is why this skips one block and not all nesting.
    """
    entry = _profile_entry(_profile_lines(target) if lines is None else lines, key)
    return None if entry is None else entry[0]


def _profile_entry(lines: list[str], key: str) -> tuple[str, list[str]] | None:
    """`(text after the colon, the block-sequence items under it)`, or None.

    The reading half of `_profile_entry_at`, which is the walk itself. Kept as
    its own name because eleven callers ask only what a declaration SAYS and
    should not have to ignore where it sits.
    """
    found = _profile_entry_at(lines, key)
    return None if found is None else (found[0], found[1])


def declared_lines(lines: list[str], key: str) -> set[int]:
    """Which LINES make up this declaration: the `key:` line and its items.

    **Item 146, and it exists so the writer stops restating the reader.**
    `learning.render` replaces a declaration and therefore has to know which
    lines to remove; it carried its own locator, and two spellings the readers
    handle broke it. A comment between two items ENDED its walk, so the rows
    below were left orphaned at top level and the file stopped being valid YAML
    - PyYAML raised on the result while the harness carried on, so item 117's
    crosscheck degraded to a printed limit rather than a refusal. A space
    before the colon made it find NOTHING, so a second declaration of the same
    key was appended and the two readers then disagreed about which one counts
    - `ProfileError`, and every one of this reader's call sites refuses the
    run. A learned rule bricked the profile it was written into.

    **Comment-stripped first, exactly as `list_from_text` does it**, and that
    is what makes this the readers' answer rather than a third one. Every
    reader reaches `_profile_entry` through lines a comment has been taken out
    of, so a comment between two items does not end their walk - and it ended
    the writer's, which is how the rows below it were orphaned. `_strip_comment`
    maps a line to a line, so a full-line comment becomes blank and the
    positions still refer to the text the caller holds.

    Comment lines are therefore NOT in the answer, and the limit is worth
    stating: a comment somebody wrote survives where they put it, even though
    the declaration it annotates has moved to the end of the file. Deleting a
    human's note to tidy up is the one thing this module's own docstring
    forbids.
    """
    found = _profile_entry_at([_strip_comment(line).rstrip() for line in lines],
                              key)
    return set(found[2]) if found else set()


def _profile_entry_at(lines: list[str],
                      key: str) -> tuple[str, list[str], set[int]] | None:
    """`(text after the colon, the block items, the LINE INDICES they occupy)`.

    **ONE locator for both spellings**, and that is the point of it rather than
    a tidiness. `profile_find` answers about a scalar and `profile_list` about a
    list, and if each found its own `key:` line they could answer about
    DIFFERENT lines of the same file - which is item 116's finding exactly,
    seven readers disagreeing about one document. They cannot here: there is one
    walk, and the list reader consumes the very rows the scalar reader stopped
    at.

    Blank lines do not end a block sequence, which is why walking
    `_profile_sections` - which drops them - is right rather than merely
    convenient. A sequence may be indented at or below its key's own
    indentation, so the test is `>=` and not `>`; `key:` followed by a
    zero-indent `- item` is legal YAML and was measured, not assumed.
    """
    rows = list(_profile_rows(lines))
    for i, (index, block, line) in enumerate(rows):
        if block in SEQUENCE_BLOCKS:
            continue
        body = line.strip()
        name, sep, rest = body.partition(":")
        if not sep or name.strip() != key:
            continue
        if value := rest.strip():
            return value, [], {index}
        depth = len(line) - len(line.lstrip())
        items: list[str] = []
        where = {index}
        for at, _block, following in rows[i + 1:]:
            lead = len(following) - len(following.lstrip())
            after = following.strip()
            if lead >= depth and (after == "-" or after.startswith("- ")):
                items.append(after[1:].strip())
                where.add(at)
            else:
                break
        return "", items, where
    return None


def profile_scalar(target: Path, key: str) -> object | None:
    """One scalar from the profile, at any depth. None when it is absent OR
    explicitly blank - a blank command means the step is skipped, and the two
    are the same instruction to a caller."""
    raw = profile_find(target, key)
    if raw is None or raw.startswith("["):
        return None
    return _scalar(raw)


def profile_list(target: Path, key: str) -> list | None:
    """One list from the profile, at any depth, in EITHER YAML spelling.

    None when the key is absent, which a caller must distinguish from `[]` -
    "no phases declared" and "no phases at all" are opposite instructions.

    **Both spellings, because YAML says they are the same list.** This read
    inline only until item 118, and the cost was measured rather than argued:
    `sca.py --propose-quarantine` drafts a BLOCK list, the shipped template
    documents a block list, and an adopter who followed those instructions
    declared NOTHING - the gate kept blocking with no explanation, because a
    reader answering None is indistinguishable from a profile that named
    nothing. PyYAML read the same file as two declarations, so the reader that
    accepted one spelling was the one inventing a second grammar; item 117
    settled that argument for `components :` and this is the same argument
    about `- `.

    **A block with no items is None, not `[]`** - `key:` with nothing under it
    is `None` in YAML too, and the difference is load-bearing for `phases`,
    where `[]` means "run no phases" and None means "nobody said". Agreeing
    with the reference reader here is the whole reason the empty case is
    called out rather than left to fall out.
    """
    return list_from_lines(_profile_lines(target), key)


def list_from_lines(lines: list[str], key: str) -> list | None:
    """`profile_list`'s answer, over lines a caller already holds.

    **Item 129 factored this out so the WRITER can ask it.** `learning.render`
    is the only module that writes `.sdlc/profile.yml`, and it had a reader of
    its own that saw the inline spelling alone - so a block-form declaration
    read as absent and the writer dropped every entry somebody had written,
    leaving their `- "x"` lines orphaned under the previous key. The consumers
    moved to both spellings at item 118 and the producer did not.

    Nothing about `profile_list` changed: it is this function over
    `_profile_lines`, which is the one door that names the file.
    """
    entry = _profile_entry(lines, key)
    if entry is None:
        return None
    raw, items = entry
    if raw.startswith("[") and raw.endswith("]"):
        return _inline_list(raw)
    if raw:
        return None                       # a scalar is not a list
    return [_scalar(item) for item in items] if items else None


def list_from_text(text: str, key: str) -> list | None:
    """`list_from_lines` over text somebody is about to rewrite.

    Comment-stripped exactly the way `_profile_source` does it, because a
    writer that read the file differently from its readers is the defect this
    exists to close rather than a nicety.
    """
    return list_from_lines([_strip_comment(line).rstrip()
                            for line in text.splitlines()], key)


#: The scalar dials `profile_dials` reads, and the modes each may take.
DIAL_MODES = {"gate_profile": GATE_PROFILES, "signing": SIGNING_MODES,
              "coverage_enforcement": ENFORCEMENT_MODES}


def profile_dial(target: Path, key: str) -> str | None:
    """One scalar dial as every reader must see it (T471): normalised, None when
    the profile leaves it unset, refused by name when it is none of its modes."""
    value = profile_top_value(target, key)
    if value is None or not str(value).strip():
        return None
    chosen = str(value).strip().lower()
    if chosen not in DIAL_MODES[key]:
        raise ProfileError(
            f"{PROFILE_NAME}: {key}: {str(value).strip()!r} is not one of "
            f"{', '.join(DIAL_MODES[key])} - read as none of them it would fall back "
            f"to the policy floor without a word")
    return chosen


def profile_dials(target: Path) -> dict:
    """The dials `tighten_only` judges, read out of `.sdlc/profile.yml` with the
    one stdlib reader this module now owns.

    **T471: each scalar dial is normalised HERE, once, and one that is none of its
    modes is refused by name.** They were kept as written, and `effective`,
    `regime` and `tighten_only` keep only values already in their mode tuples - so
    `signing: DSSE` fell to `none` while `approve.signing_mode`, which lowercased
    it, reported dsse, and the run recorded unsigned approvals at exit 0;
    `gate_profile: Regulated` fell back to the floor's regime the same way. A
    value that is no mode at all fell back without a word - the permissive
    direction, for a typo.
    """
    dials: dict = {}
    for key in DIAL_MODES:
        if (chosen := profile_dial(target, key)) is not None:
            dials[key] = chosen
    # `block_on` is nested under `review:`, which is why it goes through the
    # depth-agnostic reader rather than intake's top-level-scalar contract.
    if (listed := profile_list(target, "block_on")) is not None:
        dials["block_on"] = [str(v) for v in listed]
    return dials


# ── what an agent may not do ─────────────────────────────────────────────────


#: The placeholder a branch pattern must contain. The pattern is the ONE string
#: the worktree model and the CI ledger gate both key on: one side names the
#: branch a run publishes to, the other decides which branch is a run's. A
#: pattern only one of them knows is a merge gate that quietly stops applying.
RUN_TOKEN = "<run-id>"


def push_remotes(policy: dict) -> tuple[str, ...]:
    """The remotes agents may publish to.

    Text for the ordinary single-remote repository, a list for an estate whose
    components push to different places - a fork here, an internal mirror
    there, because many upstreams will not take a direct push. Widening it is
    still a change to the constitution, which is the point: a component may
    CHOOSE from this set and may never invent a remote nobody allowed.
    """
    value = policy["push_remote"]
    return tuple(str(v) for v in value) if isinstance(value, list) else (str(value),)


def branch_pattern(policy: dict, vcs: dict | None = None) -> str:
    """The branch pattern for one component, or the repository's default.

    Defaults to `<push_branch_prefix><run-id>`, so a repository that says
    nothing behaves exactly as it did before this existed.
    """
    declared = str((vcs or {}).get("branch_pattern", "") or "").strip()
    return declared or f"{policy['push_branch_prefix']}{RUN_TOKEN}"


def pattern_problems(policy: dict, pattern: str, where: str = "branch_pattern") -> list[str]:
    """Why this pattern cannot be used - checked where it is DECLARED, so the
    refusal names the file rather than surfacing later as a branch that no
    longer looks like a run's."""
    problems = []
    if RUN_TOKEN not in pattern:
        problems.append(f"{where} {pattern!r} does not contain {RUN_TOKEN} - every "
                        f"run would publish to the same branch")
    prefix = policy["push_branch_prefix"]
    if not pattern.startswith(prefix):
        problems.append(f"{where} {pattern!r} does not start with the policy's "
                        f"push_branch_prefix {prefix!r} - the push wall and the CI "
                        f"ledger gate both key on that prefix")
    if pattern.count(RUN_TOKEN) > 1:
        problems.append(f"{where} {pattern!r} contains {RUN_TOKEN} more than once - "
                        f"a run id read back out of it would be ambiguous")
    return problems


def branch_for(pattern: str, run_id: str) -> str:
    """The branch this run publishes to under this pattern."""
    return pattern.replace(RUN_TOKEN, run_id)


def run_of_branch(pattern: str, branch: str) -> str:
    """The run id this branch belongs to, or "" if it is not a run branch.

    The inverse of `branch_for`, and a test holds the two together. Splitting on
    the token rather than a regular expression keeps a pattern with regex
    metacharacters in it (`release/v1.2+<run-id>`) from meaning something else.
    """
    head, _, tail = pattern.partition(RUN_TOKEN)
    if len(branch) <= len(head) + len(tail):
        return ""
    if not branch.startswith(head) or not branch.endswith(tail):
        return ""
    return branch[len(head):len(branch) - len(tail) if tail else len(branch)]


def _rel(root: Path, path: Path) -> str | None:
    try:
        return (path if path.is_absolute() else root / path).resolve().relative_to(
            root.resolve()).as_posix()
    except ValueError:
        return None


def matches(rel: str, patterns: list) -> bool:
    """fnmatch, with `**/x` also matching a bare `x` at the root — the spelling
    people expect, and the one a policy author will write."""
    for raw in patterns:
        pattern = str(raw)
        if fnmatch.fnmatch(rel, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:]):
            return True
    return False


def confinement(policy: dict, root: Path, run_id: str, paths: list) -> list[str]:
    """Every write that would land outside this run's own worktree.

    A path that escapes the repository entirely is reported first and by its
    absolute form: `../../.ssh/authorized_keys` is not a subtler version of the
    same mistake.
    """
    root = Path(root)
    home = f"{policy['worktrees_root']}/{run_id}"
    problems = []
    for raw in paths:
        rel = _rel(root, Path(raw))
        if rel is None:
            problems.append(f"{raw}: outside the repository altogether")
        elif not (rel == home or rel.startswith(home + "/")):
            problems.append(f"{rel}: outside this run's worktree ({home})")
    return problems


def _as_windows_stores(rel: str) -> str:
    """`rel` with each part spelled as Windows stores it: no trailing dots or
    spaces, and no `:stream` suffix (`name::$DATA` IS the file).

    **T499.** Windows writes `.env.` as `.env`, and `resolve` normalises that
    spelling only for a file that already exists, so a protected file not yet
    created was reachable through it. A part that is nothing but dots (`.`,
    `..`) is kept as it is.
    """
    parts = []
    for part in rel.split("/"):
        stored = part.split(":", 1)[0].rstrip(". ")
        parts.append(stored or part)
    return "/".join(parts)


def protected(policy: dict, root: Path, paths: list) -> list[str]:
    """Every write to a file only the CLIs may touch - under its own spelling
    or the one Windows stores it under (T499). Both are asked on every
    platform: elsewhere the second names another file, and refusing an odd
    name costs nothing."""
    out = []
    for raw in paths:
        rel = _rel(Path(root), Path(raw)) or str(raw).replace("\\", "/")
        if (matches(rel, policy["protected_paths"])
                or matches(_as_windows_stores(rel), policy["protected_paths"])):
            out.append(f"{rel}: a protected path - the CLIs write it, agents do not")
    return out


def push_target(policy: dict, remote: str, branch: str, run_id: str = "",
                vcs: dict | None = None) -> list[str]:
    """Whether this push goes to a place this run may publish.

    `vcs` is the component's own block when there is one. A component may pick a
    remote from the constitution's set and a branch pattern that still starts
    with its prefix; it may not reach past either.
    """
    problems = []
    allowed = push_remotes(policy)
    if remote and remote not in allowed:
        problems.append(f"remote {remote!r}: agents push only to "
                        + (repr(allowed[0]) if len(allowed) == 1
                           else "one of " + ", ".join(repr(a) for a in allowed)))
    if (vcs or {}).get("role") == "fork":
        problems.append("this component's role is `fork` - a fork is read from and "
                        "never pushed to; publish to the component that owns the code")
    pattern = branch_pattern(policy, vcs)
    if bad := pattern_problems(policy, pattern):
        return problems + bad
    found = run_of_branch(pattern, branch)
    if not found:
        problems.append(f"branch {branch!r}: agents push only to "
                        f"{branch_for(pattern, '<run-id>')}")
    elif run_id and found != run_id:
        problems.append(f"branch {branch!r} belongs to another run, not {run_id}")
    return problems


#: Where one command line stops and the next begins. A wall that reads the whole
#: line as one command is bypassed by `git status && rm -rf /`, so every segment
#: is judged as its own command. `deny_git`'s substring search never had this
#: problem — it finds its token anywhere in the line — which is exactly why the
#: tokenised matcher beside it must split, or it would be the WEAKER of the two.
_SEPARATORS = re.compile(r"&&|\|\||[;\n|&]")

#: A backslash-newline is a shell line-continuation: `rm \<newline>-rf build` is
#: one command, `rm -rf build`, not two. `\n` is a separator above, so without
#: this the line splits into `rm \` (a bare `rm`, no `-r`) and `-rf build`, and
#: the wall goes quiet on a spelling a copy-paste produces by itself. Joined
#: before anything else runs.
_LINE_CONTINUATION = re.compile(r"\\\r?\n")

#: A backslash that escapes a SPACE is punctuation the shell removes before the
#: program ever sees it, so the word it appears to join was never one word.
#: It matters exactly where a value is re-split into a command: MEASURED,
#: `env --split-string=echo\ MARKER` prints MARKER, so
#: `env --split-string=rm\ -rf\ /` deletes — while the tokeniser saw `rm\`,
#: whose program name is EMPTY once the backslash is read as a path separator,
#: and `-rf\`, which stops being a short-flag cluster. Two silent misses from
#: one character.
#:
#: Removed after the line continuation above (that backslash escapes a NEWLINE,
#: not a space) and before anything is split, so the words each rule is matched
#: against are the words the program receives. The consequence, stated rather
#: than assumed: a bare `rm\ -rf\ /` — one word to a shell, and `command not
#: found` — is judged as `rm -rf /` and refused. It destroys nothing either
#: way, and between a refusal somebody argues with and a miss nobody notices
#: this file has always picked the refusal.
_ESCAPED_SPACE = re.compile(r"\\(?=[ \t])")

#: Wrappers that are not commands and hold one: a subshell group `( … )`,
#: a command substitution `$( … )`, a backtick pair, and a brace group
#: `{ … ; }`. Each is replaced by a separator BEFORE the line is cut up, so what
#: runs inside becomes a command of its own. Measured on the shipped
#: constitution: `( rm -rf build )` tokenised to `['(', 'rm', '-rf', 'build',
#: ')']`, whose program is `(` — a name no rule is about — and
#: `echo $(rm -rf build)` to `['echo', '$(rm', …]`, whose program is `echo`. A
#: parenthesis was a hat that hid `rm` from a wall which could already see
#: through `sudo`; `{ rm -rf build; }` was the same hat one bracket over.
#:
#: A brace is matched ONLY as a whitespace-delimited token, so a `{ … }` group
#: is cut while a variable `${SHELL}` and a brace expansion `build/{a,b,c}` — the
#: brace touches non-space — are left whole. A grouping brace is always its own
#: word (`{` is followed by a space, `}` preceded by one); the expansion and
#: variable forms never are.
#:
#: A QUOTE is deliberately not here, and this is the one line to get right. The
#: tokeniser below does not respect quoting: `echo "rm -rf x"` becomes
#: `['echo', 'rm', '-rf', 'x']`, allowed only because the PROGRAM is `echo`.
#: Strip quotes here too and that command is refused for printing a sentence.
#: The consequence, stated rather than assumed: a parenthesis inside a quoted
#: string (`echo "(rm -rf x)"`) IS judged, and refused. Between a refusal
#: somebody argues with and a miss nobody notices, this file has always picked
#: the refusal.
_GROUPING = re.compile(r"\$\(|[()`]|(?<![\S]){(?=[\s])|(?<=[\s])}(?![\S])")

#: Prefixes that are not the command: a privilege escalator, `VAR=value` env
#: assignments, or a wrapper that runs whatever follows it. `sudo rm -rf /` is
#: `rm -rf /` with a hat on, and so is `timeout 60 rm -rf /`.
#:
#: The value is how many POSITIONAL words the wrapper eats before its command
#: begins — `timeout` takes a duration, every other prefix here takes none.
#: A prefix's own `-flags` are skipped either way; the flags that take a
#: SEPARATE value are named per prefix in `_PREFIX_VALUE_FLAGS`, because both
#: directions of getting that wrong are a silent miss: skip `-s` without `KILL`
#: and `KILL` is judged as the program, skip one word too many and `-rf` is.
#: `stdbuf` is here for its `-oL`-style flags alone. `eval` runs its argument
#: string as a command, and `nice`/`ionice`/`setsid`/`parallel` each run the
#: command that follows them — the same hat as `sudo`, one wrapper over.
_PREFIXES = {"sudo": 0, "doas": 0, "env": 0, "command": 0, "nohup": 0,
             "time": 0, "exec": 0, "timeout": 1, "stdbuf": 0,
             "eval": 0, "nice": 0, "ionice": 0, "setsid": 0, "parallel": 0}
#: The value-taking flags of each prefix, named so the VALUE is skipped WITH the
#: flag rather than read as the program. `sudo -u root rm -rf /` ran `root`;
#: only flags that genuinely take a SEPARATE word belong here, because listing
#: one that does not (`sudo -i`, say) would eat the command instead.
_PREFIX_VALUE_FLAGS = {
    "timeout": ("-s", "--signal", "-k", "--kill-after"),
    "sudo": ("-u", "--user", "-U", "--other-user", "-g", "--group",
             "-h", "--host", "-p", "--prompt", "-r", "--role", "-t", "--type",
             "-T", "--command-timeout", "-C", "--close-from",
             "-D", "--chdir", "-R", "--chroot"),
    "doas": ("-u", "-C"),
    # NOT `-S`/`--split-string`: GNU env RE-SPLITS its value into a whole
    # sub-command, so consuming it as an opaque value would let `env -S "rm -rf
    # /"` past the wall. Left as a lone flag, the command it splits into falls
    # through and is judged. (Quotes are stripped before tokenising, so a value
    # written as a SEPARATE word cannot be isolated as one command to re-parse
    # anyway — see `_COMMAND_VALUE_FLAGS` for the attached spelling, where it
    # can be and has to be.)
    "env": ("-u", "--unset", "-C", "--chdir"),
    "nice": ("-n", "--adjustment"),
    "ionice": ("-c", "--class", "-n", "--classdata"),
    "parallel": ("-j", "--jobs"),
}

#: Flags whose value is not a value but a COMMAND, per prefix, in the spellings
#: that stick it to the flag. `_PREFIX_VALUE_FLAGS` above isolates a value only
#: when the shell already made it a SEPARATE word; nothing in this file ever
#: split `--flag=value`, which is a limitation every attached spelling shared,
#: not something the `-S` correction introduced.
#:
#: It matters here because GNU `env` re-splits this one flag's value into a
#: whole sub-command. MEASURED on the machine rather than read off a manual
#: page (GNU coreutils 8.32): `env --split-string='echo hi'`, `env -S'echo hi'`,
#: `env -Secho hi` and `env --split-string=echo\ MARKER` all RUN the command —
#: and `env --split-string="rm -rf /"` was ALLOWED by this wall, because
#: `--split-string="rm` is one token, equal to no flag and named by no rule.
#:
#: The SEPARATE-word spelling needs nothing: `env -S rm -rf /` leaves `-S`
#: standing alone and `rm` falls through as the program, which is why `-S` is
#: absent from `_PREFIX_VALUE_FLAGS` above and must stay absent. Only the
#: ATTACHED spelling is split, and only for the flags named here.
_COMMAND_VALUE_FLAGS = {"env": ("--split-string", "-S")}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _attached_value(word: str, flags: tuple[str, ...]) -> str | None:
    """The value stuck to one of `flags` inside a single word, or None.

    A long flag attaches with `=` (`--split-string=rm`); a short one attaches
    directly (`-Srm`, and `-S"rm` once the shell's quote has been typed into
    the middle of the token). A flag STANDING ALONE has no attached value and
    answers None, so the separate-word spelling keeps falling through exactly
    as it did — the two spellings are handled in two places on purpose, because
    one of them must not consume the word after it.

    **T372: a `$'...'`/`$"..."` (ANSI-C / locale-quoted) opener is stripped
    the same way a plain quote is.** This tokeniser does not respect quoting
    in general (item 157), but it already strips a LEADING quote character
    directly stuck to the flag (`-S'rm` -> `rm`) because that quote can
    never be anything else in this position. A `$` immediately followed by
    a quote is the same opener one character earlier - `str.strip` does not
    reach past the `$` on its own, since strip only removes a contiguous
    run of characters that are THEMSELVES in the given set, and `$` is not
    a quote character. MEASURED (GNU coreutils 8.32, real shell): `env
    -S$'rm -rf X'` really deletes `X` - bash expands `$'rm -rf X'` to the
    literal text `rm -rf X` with no escapes to interpret here, glued to
    `-S` with no space, so env sees exactly the same attached value as the
    plain-quoted spelling.
    """
    for flag in flags:
        if not word.startswith(flag) or word == flag:
            continue
        rest = word[len(flag):]
        if flag.startswith("--"):
            # `=` or nothing: `--split-string-ish` is a different flag, not this
            # one carrying the value `-ish`.
            if not rest.startswith("="):
                continue
            rest = rest[1:]
        if rest[:1] == "$" and rest[1:2] in ("\"", "'"):
            rest = rest[1:]
        if value := rest.strip("\"'"):
            return value
    return None


def _short_value_letters(*flag_groups: tuple[str, ...]) -> set[str]:
    """The short, single-letter spellings inside one or more flag-name
    tuples, case-folded - `("-u", "--unset")` contributes `{"u"}`. Used to
    walk a short-flag CLUSTER the way GNU getopt does: a value-taking
    option consumes the REST of the token, so only a letter reached with
    every letter BEFORE it known NOT to take a value can still mean that
    option.
    """
    return {f[1].lower() for group in flag_groups for f in group
            if len(f) == 2 and f[0] == "-" and f[1].isalpha()}


def _declustered(word: str, command_flags: tuple[str, ...],
                 value_flags: tuple[str, ...]) -> str:
    """`word` rewritten so a COMMAND-carrying short flag clustered behind
    other short flags of the SAME prefix is at the front, in the shape
    `_attached_value` already understands - or `word` unchanged if it is
    not that shape.

    **T372.** GNU env clusters short options exactly like any other GNU
    program, so `-viSrm` is `-v -i -S rm` and `-S`'s attached value is
    `rm`. MEASURED (GNU coreutils 8.32, real shell, `-v` for env's own
    trace): `env -vS'rm -rf X'` really deletes `X` (`executing: rm`), and
    - with an absolute path so `-i`'s environment wipe cannot hide the
    result - `env -iS/usr/bin/rm -rf X` does too. The old tokeniser only
    recognised the command flag as the FIRST thing in the word
    (`word.startswith(flag)` in `_attached_value`), so any boolean flag
    clustered in front of it hid the command completely.

    Walks the cluster letter by letter: a letter that is itself a
    VALUE-taking option for this prefix (`value_flags`, e.g. env's `-u`/
    `-C`) consumes the rest of the token as ITS OWN value, so scanning
    stops there without rewriting - a command letter only reachable past
    one of those is genuinely not reachable in real getopt either, and
    `env -uSrm ...` really unsets a variable named `Srm`, not a command.
    (`-0` is env's OWN `--null`, and real env refuses `-0` together with
    any command at all - MEASURED, `env -0Srm -rf X` and `env -iv0S/usr/
    bin/rm -rf X` both answer `cannot specify --null (-0) with command`
    and destroy nothing. Rewriting `-0S...` anyway is harmless - it only
    makes an already-refused-by-env spelling ALSO refused here, which
    this file has always preferred over the alternative.)
    """
    if not (word.startswith("-") and not word.startswith("--") and len(word) > 2):
        return word
    command_letters = _short_value_letters(command_flags)
    blocking_letters = _short_value_letters(value_flags)
    for i, ch in enumerate(word[1:], 1):
        low = ch.lower()
        if low in command_letters:
            return f"-{ch}{word[i + 1:]}"
        if low in blocking_letters:
            return word
    return word


def _program(token: str) -> tuple[str, str]:
    """A command's name and its FAMILY, without directory, extension or case.

    `System32\\cmd.exe`, `/bin/rm` and `RM` are the same program to a wall, and
    a rule that only knew one spelling would be a rule an agent steps around by
    typing the path.

    The family is the name cut at its first dot, because `mkfs.ext4`,
    `mkfs.xfs` and `mkfs.btrfs` are one tool with a filesystem on the end —
    measured, not assumed: `mkfs.ext4 /dev/sda1` walked straight past a rule
    named `mkfs` until this existed.

    A program named through a shell variable is unwrapped to the variable's
    NAME: `${SHELL}` and `$SHELL` become `shell`, so a shell reached through its
    own variable (`${SHELL} -c 'rm -rf x'`) is the interpreter it names rather
    than a token no rule is about. Only the program word is unwrapped; a `${VAR}`
    used as an ARGUMENT is never a program and never reaches here.
    """
    name = token.replace("\\", "/").rsplit("/", 1)[-1].strip("\"'").lower()
    if name.startswith("$"):
        name = name[1:].strip("{}")
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".sh"):
        name = name.removesuffix(suffix)
    return name, name.split(".", 1)[0]


def _segment_words(segment: str) -> list[str]:
    """One segment's tokens, unquoted, with the prefixes that are not the
    command dropped.

    Quotes come off because a nested command arrives wearing them:
    `powershell -Command "Remove-Item -Recurse x"` tokenises to `"Remove-Item`,
    which equals nothing a rule can name.

    A prefix that takes arguments takes them with it — see `_PREFIXES`. A flag
    that carries a COMMAND instead of a value hands it back to be judged — see
    `_COMMAND_VALUE_FLAGS`.
    """
    words = [w.strip("\"'") for w in segment.split()]
    words = [w for w in words if w]
    while words:
        if _ASSIGNMENT.match(words[0]):
            words.pop(0)
            continue
        name = _program(words[0])[0]
        if name not in _PREFIXES:
            break
        value_flags = _PREFIX_VALUE_FLAGS.get(name, ())
        command_flags = _COMMAND_VALUE_FLAGS.get(name, ())
        positionals = _PREFIXES[name]
        words.pop(0)
        while words and words[0].startswith("-"):
            word = _declustered(words[0], command_flags, value_flags)
            if (carried := _attached_value(word, command_flags)) is not None:
                # The flag's value IS the command - but that value can ITSELF
                # begin with one of the wrapper's own options, and env's split
                # string is the case that matters: `env -S'-v rm -rf victim'`
                # carries `-v rm -rf victim`, and `-v` is env's own verbose
                # flag, not the program. Put the carried text back as the
                # first word and zero the positionals (nothing may eat this
                # command as an ordinary argument), but CONTINUE the loop
                # rather than stop - the same option-skipping below (a bare
                # flag popped on its own, `-u`/`--unset`/`-C`/`--chdir` eating
                # a separate following word) applies to the RECONSTRUCTED
                # words exactly as it applies to `env -u FOO rm -rf /`, so one
                # loop now answers both, and a carried value that is itself
                # another attached flag (`-S-S...`) is re-split the same way.
                # Terminating: each pass through this branch strips at least
                # `len(flag)` characters, so the word strictly shortens.
                words[0] = carried
                positionals = 0
                continue
            if words.pop(0) in value_flags and words:
                words.pop(0)
        # Never eat the last word: a wrapper with nothing after it is a wrapper
        # with no command, not a command spelled `-rf`.
        while positionals and len(words) > 1:
            words.pop(0)
            positionals -= 1
    return words


#: `find . -exec rm -rf {} \;` runs `rm` without `rm` ever being the segment's
#: program. The words after it are a command, so they are judged as one.
#:
#: Read only through `_exec_carried_command`, and only when the segment's OWN
#: program is `find` — see the call in `_commands_in`. These four are find's
#: own predicates, not general flags, and scanning for the `=`-attached
#: spelling in ANY segment (as this file did before this sentence) is a
#: second wall escape: `xargs -exec=foo rm -rf build` has nothing to do with
#: find, but a stray `-exec=` token anywhere in the words list `=`-partitions
#: to `-exec` all the same, is read as a carried command, and PRE-EMPTS the
#: xargs-specific lookup below that would have found the real program.
#: MEASURED: `xargs -exec=foo rm -rf build`, `xargs rm -rf -- -exec=foo` and
#: `find . -print0 | xargs -0 rm -rf -- -exec=x` each deleted a real
#: directory while this rule fired on the decoy and never reached `rm`. The
#: `=` spelling protects nothing real in return — `find -exec=` is an unknown
#: predicate GNU find refuses to run — so gating this scan to `find` costs
#: nothing and closes the escape.
_EXEC = ("-exec", "-execdir", "-ok", "-okdir")

#: `xargs` is the same carrier with the command at the other end: in
#: `find . -name '*.py' | xargs rm -rf` the segment's program is `xargs`, and
#: `rm` is an argument. Its own options stand in between, so they are skipped.
#:
#: RECOGNISED, so the gap is a list rather than an assumption: these flags take
#: a SEPARATE argument and it is skipped with them, and every other `-…` word is
#: taken as a flag standing alone — which is right for `-0`, `-r`, `-t`, `-p`,
#: `-x`, `--null`, `--no-run-if-empty`, and for the attached forms `-n5`,
#: `-I{}` and `--max-args=5`. The optional-argument spellings `-i`, `-e`, `-l`,
#: `--replace`, `--eof` and `--max-lines` are NOT here on purpose: GNU xargs
#: attaches their argument when there is one, so skipping a word after them
#: would eat the command. The first word that is not one of these begins the
#: command xargs runs.
_XARGS_VALUE_FLAGS = ("-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s",
                      "--arg-file", "--delimiter", "--max-args",
                      "--max-procs", "--max-chars")


def _xargs_command_at(words: list[str]) -> int | None:
    """Where `xargs`'s own options stop and the command it runs begins."""
    i = 1
    while i < len(words):
        if not words[i].startswith("-"):
            return i
        i += 2 if words[i] in _XARGS_VALUE_FLAGS else 1
    return None

#: An interpreter handed a command string runs it, and the wall must see THAT
#: command. This is the Rev-5 incident's own shape — `Bash(powershell:*)` beside
#: the git rules it was meant to reinforce — met again one layer down: without
#: this, `cmd /c rmdir /s /q x` is a command whose program is `cmd`. `shell` is
#: here for the `$SHELL`/`${SHELL}` form: `_program` unwraps the variable to its
#: name, so `${SHELL} -c 'rm -rf x'` is judged as the shell it names.
_INTERPRETERS = ("cmd", "powershell", "pwsh", "sh", "bash", "zsh", "dash", "ksh",
                 "shell")
_RUN_FLAGS = ("/c", "/k", "-c", "-command", "-comman", "-comm", "-com")
#: The interpreters whose single-letter options CLUSTER, as POSIX shells' do.
_POSIX_SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "shell")
_SHORT_CLUSTER = re.compile(r"-[A-Za-z]{2,}")
#: A POSIX shell's own options that take a SEPARATE value word, so the word
#: after is that value and not the command string (review F6): `-o pipefail`,
#: `--rcfile FILE`. Everything else that starts with `-` or `+` is a flag on
#: its own, including `+x` and clustered short flags. `-O`/`+O` (bash's shopt
#: toggles, `bash -O extglob -c ...`) take a value the same way (review F9).
_SHELL_VALUE_FLAGS = ("-o", "+o", "-O", "+O", "--rcfile", "--init-file")


def _shell_command_at(words: list[str], program: str) -> int | None:
    """The index of the command string a POSIX shell would run, or None.

    **T493.** A POSIX shell reads its OWN options first and then takes the
    FIRST operand as the command string - but only when `-c` is among those
    options (bare, or clustered as `-lc`); without `-c` the first operand is a
    script FILE, whose arguments are not a command to judge. This walks the
    options exactly as the shell does and returns that operand's index, so the
    caller judges the ORIGINAL words from there.

    Two earlier versions each opened a hole a test now covers. The first
    rewrote every `-...c...` word to `-c` and handed the rewritten words on,
    erasing find's `-exec` and a `-recurse` flag from the command being judged
    (review F1). The second stopped at the first cluster, so a cluster followed
    by a separate `-c` (`bash -lc -c 'CMD'`) handed on `-c CMD`, whose own
    program is `-c` and whose payload was never read (review F5). Walking to the
    first operand, the shell's own rule, is what closes both: the command is the
    operand, wherever the run flag sits among the options before it.
    """
    if program not in _POSIX_SHELLS:
        return None
    saw_c = False
    i = 1
    while i < len(words):
        word = words[i]
        if not (word.startswith("-") or word.startswith("+")):
            return i if saw_c else None          # the first operand
        if word == "-c" or (_SHORT_CLUSTER.fullmatch(word) and "c" in word[1:].lower()):
            saw_c = True
        i += 2 if word in _SHELL_VALUE_FLAGS else 1
    return None                                   # options only, nothing to run


def _carried_command(words: list[str], flags: tuple[str, ...],
                     start: int = 0) -> list[str] | None:
    """The command a carrier flag hands to another program, as its own words.

    TWO spellings, because a matcher that only knew the first was walked past.
    The command is usually the NEXT word (`cmd /c rmdir /s /q x`); it can also
    be STUCK to the flag by an `=`, and that spelling is not theoretical.
    MEASURED on Windows: `cmd.exe /c=rmdir /s /q DIR` deleted a real directory
    and exited 0, because `cmd` treats `=` as an argument delimiter — while
    this wall compared `/c=rmdir` against whole words, matched nothing, and
    allowed it.

    The other carriers were measured too, so the coverage is a finding rather
    than an assumption: `powershell -Command=…`, `powershell -Command:…`,
    `bash -c=…` and `sh -c=…` each FAIL to run, and `find` answers
    `unknown predicate '-exec='`. They are read here anyway. One rule for every
    carrier is a rule nobody has to remember per flag, and a spelling that
    cannot run destroys nothing, so refusing it costs nothing.
    """
    for i, word in enumerate(words[start:], start):
        low = word.lower()
        if low in flags and i + 1 < len(words):
            return words[i + 1:]
        flag, sep, value = low.partition("=")
        if sep and value and flag in flags:
            return [word.split("=", 1)[1], *words[i + 1:]]
    return None


def _bare_first_carried_command(words: list[str], flags: tuple[str, ...],
                                start: int = 0) -> list[str] | None:
    """The command a carrier flag hands to another program - BARE spelling
    first, across the WHOLE list, before the `=` spelling is even
    considered. See `_exec_carried_command` for why a single combined pass
    (bare-or-`=` at each word, left to right - what `_carried_command` alone
    does) is not enough: an `=`-decoy placed BEFORE the real bare flag wins
    that race. The race is the same shape for every carrier - find's exec
    family there, and an interpreter's own run flag in `_commands_in`'s
    interpreter branch - so it is closed once here rather than per caller.
    """
    for i, word in enumerate(words[start:], start):
        if word.lower() in flags and i + 1 < len(words):
            return words[i + 1:]
    return _carried_command(words, flags, start)


def _exec_carried_command(words: list[str]) -> list[str] | None:
    """The command find's `-exec`/`-ok`/`-execdir`/`-okdir` hands to another
    program - BARE spelling first, over the WHOLE list, before the `=`
    spelling is even considered.

    A single combined pass (try bare-or-`=` at each word, left to right) lets
    a decoy `=`-token placed BEFORE the real one win the race: on
    `find . -exec=foo -exec rm -rf x \\;`, a combined scan matches
    `-exec=foo` at the first `-exec…` word it sees and hands back
    `['foo', '-exec', 'rm', '-rf', 'x', '\\;']`. Reached ALONE that recurses
    with `foo` as the program, `foo` is not `find`, and the real `-exec`
    sitting right behind it is never read. Reached through `_commands_in`,
    where the gate is INHERITED rather than re-derived (see `exec_ok`
    there), the same recursive call still carries the gate forward, finds
    the real `-exec` one hop later, and arrives at the same answer - so the
    two passes here are not independently load-bearing for THIS shape; they
    are a second, cheaper route to it, and the one that matches how the OLD
    exact-match-only scan (which never recognised `=` at all) walked past
    the decoy in a single pass rather than a recursive one.

    NOT equivalent to the combined scan - measured, not argued. An earlier
    version of this docstring claimed mutating this function down to
    `return _carried_command(words, _EXEC)` (bare-and-`=` combined, no
    priority) was PROVEN equivalent because `_commands_in`'s inherited gate
    rescues it one recursion later for every reachable case. That rescue
    assumes the recursive call reaches the exec scan, and it does not when
    the decoy's own extracted value names an INTERPRETER:
    `find . -exec=sh -exec rm -rf x -c echo hi \\;` recurses into
    `sh -exec rm -rf x -c echo hi \\;`, whose program is `sh`, so
    `_commands_in` takes the INTERPRETER branch instead of the exec one,
    binds ITS OWN `nested` from the trailing `-c echo hi`, and the real
    `-exec rm -rf x` is never read. Killed by
    `test_the_bare_first_pass_is_not_equivalent_to_the_combined_scan` in
    test_policy.py - eleven inputs, all blocked at the round-2 fix and all
    allowed by the mutant above. The bare-first pass is independently
    load-bearing.

    The `=` spelling still has to run somewhere, for `find . -exec=rm -rf x
    +` (no bare token present at all) - but only as a FALLBACK, once a full
    pass finds no bare `-exec` anywhere in the list. Delegates to
    `_bare_first_carried_command`, which the interpreter branch of
    `_commands_in` now shares - the same decoy race, one function over,
    closed once instead of twice (review-W350.md, finding 1).
    """
    return _bare_first_carried_command(words, _EXEC)


def _commands_in(segment: str, exec_ok: bool = False) -> list[list[str]]:
    """Every command a single segment actually runs: itself, and anything it
    hands to another program to run.

    `exec_ok` says whether an EXEC-family token found IN THIS segment may
    still be read as find's own carrier even though the segment's own first
    word is no longer literally `find`. It starts False on every fresh call
    and becomes True either because THIS segment's program is `find`, or
    because it was handed down from a caller that reached this segment by
    peeling one EXEC-family token off already - never from an interpreter or
    an xargs extraction, which each re-derive it fresh from the new
    segment's own program (and MUST claim their own segment first - see the
    ORDER below, not just the `exec_ok` value, for why).

    The inheritance is load-bearing, not decoration. Re-deriving the gate
    from `words[0]` at every level breaks on TWO stacked EXEC-family tokens:
    `find . -exec -exec rm -rf x \\;` peels its first `-exec` and is left
    with `-exec rm -rf x \\;`, whose own first word is `-exec` — gating
    THAT recursive call on its own program name would stop right there and
    hide `rm`, exactly the shape of hole a decoy on an xargs line opens one
    level up. Carrying the flag down (but ONLY down the exec-extraction path)
    lets the second token peel too, converging with how this file always
    walked an unbroken chain of wrappers, while an xargs (or any other)
    segment reached independently still starts at False and stays gated.

    But inheritance alone is not enough - the ORDER of the checks below has
    to match the claim above, and moving the xargs check earlier is what
    makes it true rather than aspirational. `find . -exec xargs rm -rf {}
    -- -exec=foo \\;` peels find's `-exec` and hands `exec_ok=True` to the
    segment `xargs rm -rf {} -- -exec=foo` - whose OWN program is `xargs`,
    not `find`, but the inherited flag was still True. Checking the EXEC
    scan before the xargs-specific one let the inherited flag treat that
    segment's own trailing `-exec=foo` as ANOTHER carrier, extracting `foo`
    and never calling `_xargs_command_at` at all - the exact shape finding 2
    closed, reopened one level of nesting deeper by the fix for the OTHER
    hole. MEASURED: blocked at the pre-branch source, allowed at the round-1
    fix (expected - it is finding 2's own shape) but ALSO allowed by this
    fix's first draft, found only by the mutation pass. The xargs check
    below runs FIRST now,
    unconditionally on the segment's own program - exactly as the
    interpreter check above it already did - so a segment that IS an xargs
    invocation always resolves through `_xargs_command_at`, and `exec_ok`'s
    inheritance is left to do the one job it is for: peeling a chain of
    exec-family tokens that are not xargs.
    """
    words = _segment_words(segment)
    if not words:
        return []
    found = [words]
    nested: list[str] | None = None
    program = _program(words[0])[0]
    if program in _INTERPRETERS:
        # A POSIX shell's command is its first operand once `-c` is among its
        # options, wherever that flag sits (T493) - asked as the shell parses
        # it, so a later bare `-c` is an argument, not a decoy that wins the
        # race. `cmd`/`powershell` are not POSIX shells: `_shell_command_at`
        # returns None for them and the carrier scan reads their `/c`/`-Command`
        # exactly as before.
        at = _shell_command_at(words, program)
        nested = (words[at:] if at is not None
                  else _bare_first_carried_command(words, _RUN_FLAGS, 1))
    if nested is None and program == "xargs":
        at = _xargs_command_at(words)
        nested = None if at is None else words[at:]
    exec_ok = exec_ok or program == "find"
    via_exec = False
    if nested is None and exec_ok:
        # Gated (directly or by inheritance) to a find chain, not scanned
        # across every segment - see `_EXEC`. Ungated, a decoy `-exec=`
        # token in an unrelated command (an xargs argv, an argument after
        # `--`) pre-empts the xargs lookup above and hides the real program
        # behind it - which is why xargs is checked FIRST, not gated behind
        # `exec_ok` at all.
        nested = _exec_carried_command(words)
        via_exec = nested is not None
    if nested is not None:
        found.extend(_commands_in(" ".join(nested), exec_ok=via_exec))
    return found


def _short_letters(words: list[str]) -> set[str]:
    """Every letter appearing in a short-flag CLUSTER.

    `-rf`, `-fr`, `-Rf` and `-r -f` are one operation spelled four ways, and a
    matcher that cannot see that is a matcher an agent defeats by reordering two
    characters. Case-folded, so `-R` counts as `r`.

    **T372.** A flag that takes a VALUE can be attached directly (`-s0`,
    GNU truncate's short form of `--size=0`) rather than standing alone,
    and the value need not be alphabetic. Only the LEADING alphabetic run
    after the dash is ever flag letters - `-s0`'s `0` is `-s`'s value, not
    a second flag. Requiring the WHOLE word to be alphabetic (the previous
    shape) threw the flag away along with its value: MEASURED,
    `truncate -s0 FILE` matched no alternative of `truncate -s|--size|
    --size=` at all (letters stayed empty) and the file was emptied with
    this wall watching, while the separate-word spelling `truncate -s 0
    FILE` was already correctly blocked.
    """
    out: set[str] = set()
    for word in words:
        if word.startswith("-") and not word.startswith("--") and len(word) > 1:
            prefix = []
            for ch in word[1:]:
                if not ch.isalpha():
                    break
                prefix.append(ch)
            out.update("".join(prefix).lower())
    return out


def _requirement_met(alternatives: str, words: list[str], letters: set[str]) -> bool:
    """Whether any spelling of one requirement is present in this segment.

    **T366: a `--long` alternative also matches any prefix `getopt_long`
    would expand back to it.** `getopt_long` accepts any UNAMBIGUOUS prefix
    of a long option, and `rm` has no other long option starting with `r`,
    so `--r`, `--re`, `--rec`, ..., `--recursiv` all mean `--recursive`.
    MEASURED on GNU coreutils 8.32: `rm --rec -f <dir>` destroyed a
    populated directory at exit 0, while the old exact-string match left
    every abbreviation allowed.

    This file does not know a program's full option set - that would be a
    grammar, not a declared rule - and it does not need to: if a prefix is
    genuinely AMBIGUOUS for the real program, `getopt_long` itself refuses
    to run the command, so treating an abbreviation as a match here is
    always safe - it can never block a command that would otherwise have
    run, and it always blocks one that, if it runs, does what the rule
    denies. DECLARED shape, not a grammar: a `--X` token (attached `=value`
    or bare) matches alternative `--Y` (or `--Y=`, the attached-value
    spelling of the same option) when `Y` starts with `X` and `X` is
    non-empty.
    """
    for alt in alternatives.split("|"):
        alt = alt.strip().lower()
        if not alt:
            continue
        if len(alt) == 2 and alt.startswith("-") and alt[1].isalpha():
            if alt[1] in letters:            # a short flag, cluster-aware
                return True
            continue
        if alt.startswith("--"):
            # `--size` and `--size=` name the SAME option under two rule
            # spellings (bare vs. attached-value); strip either down to one
            # canonical name so an abbreviation of it is only written once.
            name = alt[2:-1] if alt.endswith("=") else alt[2:]
            if name:
                for w in words:
                    wl = w.lower()
                    if not wl.startswith("--"):
                        continue
                    head = wl[2:].partition("=")[0]
                    if head and name.startswith(head):
                        return True
            continue
        if alt.endswith("="):                # `of=` matches `of=/dev/sda`
            if any(w.lower().startswith(alt) for w in words):
                return True
            continue
        if any(w.lower() == alt for w in words):
            return True
    return False


def denied_command(policy: dict, command: str) -> list[str]:
    """Whether a command destroys data, in any shell this harness runs under.

    **The gap this closes, stated plainly.** `deny_git` is what its name says:
    git operations. It is a substring search, which suits git because its
    destructive verbs are distinctive — but that mechanism cannot be pointed at
    `rm` without either missing most spellings of it or firing on innocent ones.
    Measured on the defaults before this existed: 22 of 24 destructive shell
    spellings were allowed, and adding `"rm -rf"` to the substring list would
    have refused `confirm -rf plan.txt`, because `rm -rf` is literally inside it.

    So this list is matched by TOKENS instead. Each rule is a program followed
    by requirements, all of which must be present; a requirement may name
    several spellings separated by `|`. Short flags are matched by LETTER, so
    `-rf`, `-fr`, `-Rf` and `-r -f` are one rule, and the program is compared
    without its directory, extension or case, so `/bin/rm` is `rm`.

    **Recursion alone is the trigger, not recursion plus force.** `-f` only
    suppresses the prompts; `rm -r tree` deletes the tree either way, and a rule
    that required both would be a silent miss on the plainer spelling. The
    consequence is deliberate and worth stating: `rm -rf node_modules` is
    refused. An agent that needs a tree cleared asks, or the component's own
    declared build command does it — and that command runs from the profile, not
    through this door. Between a refusal somebody argues with and a miss nobody
    notices, this project has picked the refusal every time.

    **What this deliberately does NOT cover, so the gap is a sentence rather
    than an assumption:**

    * *Permission changes* — `chmod -R 777 /`, `chown -R`. Destructive of a
      different thing (privilege, not data); walling them belongs with the
      confinement rules, not here.
    * *Shell redirection* — `> important.log` truncates a file, and redirection
      is also how every legitimate command writes one. A rule on `>` is a rule
      on everything.
    * *Remote code execution* — `curl … | sh`. Real, and not destruction; it
      needs its own wall with its own name rather than being smuggled into this
      one.
    * *PowerShell `-EncodedCommand`* — base64 hides the nested command from the
      interpreter unwrapping below. Named here because a wall that silently
      cannot see something is the failure this file exists to avoid.
    """
    problems = []
    seen: set[str] = set()
    joined = _ESCAPED_SPACE.sub("", _LINE_CONTINUATION.sub(" ", str(command)))
    for segment in _SEPARATORS.split(_GROUPING.sub(";", joined)):
        for words in _commands_in(segment):
            name, family = _program(words[0])
            letters = _short_letters(words[1:])
            for rule in policy.get("deny_commands", []):
                parts = str(rule).split()
                if not parts or _program(parts[0])[0] not in (name, family):
                    continue
                if all(_requirement_met(req, words[1:], letters)
                       for req in parts[1:]) and rule not in seen:
                    seen.add(rule)
                    problems.append(f"{command!r}: matches the denied operation "
                                    f"{rule!r} - it destroys data outside version "
                                    f"control, which no gate can undo")
    return problems


def destructive(policy: dict, command: str) -> list[str]:
    """Whether a command destroys history, someone else's work, or data.

    TWO lists with two matching rules, because they are two different problems.
    `deny_git` is a substring search over git's distinctive verbs, unchanged
    since item 35 and still the right shape for them. `deny_commands` is
    tokenised — see `denied_command` for why the first mechanism cannot be
    pointed at `rm`. Both are reported here so one door answers one question.
    """
    flat = " ".join(str(command).split())
    return [f"{command!r}: matches the denied git operation {token!r}"
            for token in policy["deny_git"] if str(token) in flat] \
        + denied_command(policy, command)


def budget(policy: dict, diff_text: str) -> list[str]:
    """Whether one batch is small enough for a human to actually review.

    Counts the changed FILES and the added-or-removed lines, minus the exempt
    paths, because a lockfile nobody reads should not push a real change over
    the limit — and the count says what it excluded.
    """
    files, lines, exempt = [], 0, 0
    current_exempt = False
    for line in diff_text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            rel = parts[1].strip() if len(parts) == 2 else ""
            current_exempt = matches(rel, policy["budget_exempt"])
            (files.append(rel) if not current_exempt else None)
            exempt += 1 if current_exempt else 0
            continue
        if not current_exempt and line[:1] in "+-" and not line.startswith(("+++", "---")):
            lines += 1

    problems = []
    max_files = policy["max_files_per_batch"]
    max_lines = policy["max_lines_per_batch"]
    tail = f" ({exempt} exempt file(s) not counted)" if exempt else ""
    if max_files and len(files) > max_files:
        problems.append(f"{len(files)} files in one batch, budget {max_files}{tail} - "
                        f"split the work; a gate nobody can read is not a gate")
    if max_lines and lines > max_lines:
        problems.append(f"{lines} changed lines in one batch, budget {max_lines}{tail} "
                        f"- split the work")
    return problems


def head_author(target: Path) -> str:
    """Who wrote what is being approved, as git records it. Empty when there is
    no commit yet — which `self_approval` treats as unevaluable, not as fine."""
    try:
        proc = subprocess.run(["git", "-C", str(target), "log", "-1", "--format=%ae"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def self_approval(policy: dict, approver: str, author: str) -> list[str]:
    """Whether this person may approve this work.

    Under `forbid`, an unknown author is a REFUSAL. A control whose input is
    missing has not been satisfied — waving it through would make `forbid` mean
    "forbid, unless we could not tell", which is the state an attacker arranges.
    """
    if policy["self_approval"] != "forbid":
        return []
    if not author:
        return ["policy sets self_approval: forbid, and there is no recorded author "
                "to compare against - pass --author, or commit the work first"]
    if approver.strip().lower() == author.strip().lower():
        return [f"policy sets self_approval: forbid - {approver} wrote this change "
                f"and may not also approve it"]
    return []


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="The repository's SDLC guardrail policy: show it, validate it, "
                    "and check a proposed action against it (item 35).")
    ap.add_argument("--target", metavar="DIR", default=".")
    ap.add_argument("--show", action="store_true",
                    help="the effective policy, and where each part came from")
    ap.add_argument("--validate", action="store_true",
                    help="the policy file and the profile's dials; exit 1 on any "
                         "problem")
    ap.add_argument("--file", metavar="PATH",
                    help="validate this file instead of the target's own")
    ap.add_argument("--run", metavar="RUN_ID", default="")
    ap.add_argument("--write", metavar="PATH", action="append", default=[],
                    help="a path an agent proposes to write (repeatable)")
    ap.add_argument("--diff", metavar="PATH", help="a patch to measure against the "
                                                   "batch budget")
    ap.add_argument("--push", metavar="REMOTE/BRANCH", help="a proposed push target")
    ap.add_argument("--git", metavar="COMMAND", action="append", default=[],
                    help="a git command to check for destructive operations")
    ap.add_argument("--approver", metavar="WHO", default="")
    ap.add_argument("--author", metavar="WHO", default=None,
                    help="who wrote the change (default: the target's HEAD author)")
    ap.add_argument("--who", metavar="PHASE",
                    help="who runs this phase (a word, e.g. implementation) "
                         "under the declared routing; exit 1 on a routing "
                         "refusal, 0 with 'unmapped' when nothing is declared")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2

    if args.who:
        # The routing question stands alone: it is asked while setting a
        # repository up, before there is a run to measure walls against.
        try:
            routed = runner_for(target, args.who)
        except RunnerError as exc:
            print(f"!! {exc}")
            return 1
        except (PolicyError, OSError) as exc:
            print(f"!! {exc}")
            return 2
        if routed is None:
            print(f"{args.who}: unmapped - whoever is driving drives")
        else:
            print(f"{args.who}: {routed['name']} ({routed.get('capabilities')})"
                  f" -> {routed.get('command', '')}")
        return 0
    try:
        if args.file:
            path = Path(args.file)
            text = path.read_text(encoding="utf-8", errors="replace")
            doc = parse(text, path.name)
            if disagreement := crosscheck(text, doc, path.name):
                raise PolicyError(disagreement)
            if problems := validate(doc, path.name):
                raise PolicyError("; ".join(problems))
            policy = {**DEFAULTS, **doc}
        else:
            policy = load(target)
        # The PROFILE is read inside this door too, and it was not: these three
        # calls sat below the `except` and an unreadable profile left as a
        # traceback — out of the module whose entire job is refusing a governing
        # document it cannot read. Found by SPAWNING the CLI rather than by
        # calling `main`, which is item 96's rule earning itself again.
        source = args.file or (str(locate(target)) if locate(target)
                               else "built-in defaults (no policy.yml in this "
                                    "repository)")
        dials = profile_dials(target)
        overrides = gate_overrides(target)
        components = component_count(target)
    except (PolicyError, OSError) as exc:
        print(f"!! {exc}")
        return 2

    findings: dict[str, list[str]] = {
        "floor": tighten_only(policy, dials, components, overrides)}

    if args.write:
        findings["protected"] = protected(policy, target, args.write)
        if args.run:
            findings["confinement"] = confinement(policy, target, args.run, args.write)
    if args.diff:
        try:
            findings["budget"] = budget(
                policy, Path(args.diff).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"!! {exc}")
            return 2
    if args.push:
        remote, _, branch = args.push.partition("/")
        findings["push"] = push_target(policy, remote, branch, args.run)
    for command in args.git:
        findings.setdefault("git", []).extend(destructive(policy, command))
    if args.approver:
        author = args.author if args.author is not None else head_author(target)
        findings["self_approval"] = self_approval(policy, args.approver, author)

    problems = [p for rows in findings.values() for p in rows]
    if args.as_json:
        print(json.dumps({"source": source, "policy": policy, "profile": dials,
                          "components": components, "gates": overrides,
                          "effective": effective(policy, dials, overrides)
                          if not findings["floor"] else None,
                          "findings": findings, "problems": problems},
                         indent=2, sort_keys=True, default=str))
        return 1 if problems else 0

    if args.show:
        print(f"policy source: {source}")
        for key in sorted(policy):
            print(f"  {key}: {policy[key]}")
        print(f"profile dials: {dials or '(none set)'}"
              + (f", {components} component(s)" if components else "")
              + (f", gate overrides {overrides}" if overrides else ""))
        if not findings["floor"]:
            for key, value in effective(policy, dials, overrides).items():
                print(f"  effective {key}: {value}")

    for group, rows in findings.items():
        for row in rows:
            print(f"  !! [{group}] {row}")
    for limit in profile_limits(target):
        print(f"  .. [not checked] {limit}")
    if problems:
        print(f"REFUSED - {len(problems)} policy problem(s).")
        return 1
    if args.validate or args.show or not (args.write or args.diff or args.push
                                          or args.git or args.approver):
        print("OK - within policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
