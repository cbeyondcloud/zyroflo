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
                      "truncate -s"],
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
        return body.replace('\\"', '"').replace("\\\\", "\\") if text[0] == '"' else body
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
    """Drop a trailing `#` comment that is not inside quotes."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
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
    """
    items: list[str] = []
    buf: list[str] = []
    quote = ""
    for char in inner:
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(char)
        elif char == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(char)
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
                raise PolicyError(f"{name}:{n}: {key!r} is set twice - which one is "
                                  f"the policy?")
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

    Counted from the shape `render_components` writes — one `- name:` per entry at
    the block's own indent. The generator owns that contract, and reading it here
    is cheaper and more honest than a second YAML parser for a list of maps this
    module's reader deliberately refuses.
    """
    return sum(1 for line in _profile_block(target, "components")
               if line.strip().startswith("- name:"))


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
    for block, line in _profile_sections(lines):
        lead = len(line) - len(line.lstrip())
        if not _indented(line):
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
        if body.startswith("- name:"):
            found.append({"name": _unquote(body[len("- name:"):]), "path": "",
                          "role": "authoritative", "vcs": {}})
            in_vcs, vcs_depth = False, -1
            pending, pending_depth = "", -1
            continue
        if not found:
            continue
        # **Item 130: a sequence item belongs to the key that opened the block.**
        # These rows carry no colon, so they fell through the `if not sep`
        # guard below and were dropped on the floor while the key itself stored
        # the empty string - which `suite.quarantined` reads as "nobody declared
        # one", substituting the WORKSPACE list. A component lost its own
        # declaration AND inherited one nobody accepted for it, in one step.
        if pending and lead >= pending_depth and (body == "-"
                                                  or body.startswith("- ")):
            found[-1].setdefault(pending, []).append(_scalar(body[1:].strip()))
            continue
        pending, pending_depth = "", -1
        if body.rstrip() == "vcs:":
            in_vcs, vcs_depth = True, lead
            continue
        key, sep, value = body.partition(":")
        if not sep:
            continue
        key, value = key.strip(), _unquote(value)
        if in_vcs and key in VCS_KEYS:
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
    if found:
        return found
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
    already seen. A declaration with no name is skipped the way a component
    with none would be — there is nothing to route to it by.
    """
    found: list[dict] = []
    for line in _profile_block(target, "runners", lines):
        body = line.strip()
        if body.startswith("- name:"):
            found.append({"name": _unquote(body[len("- name:"):])})
            continue
        if not found:
            continue
        key, sep, value = body.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key in RUNNER_KEYS:
            found[-1][key] = (_inline_list(value) if key == "phases"
                              else _unquote(value))
    return [r for r in found if r.get("name")]


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
            out[key.strip()] = _unquote(value.strip())
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
            key, value = key.strip(), _unquote(value)
            out[key] = value in ("true", "True") if key == "deny_raw_git" else value
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


def hard_gates(policy: dict, profile: dict, overrides: dict | None = None) -> list[str]:
    """Which gates halt this run. A preset's set, plus anything an override made
    hard — never minus anything, which `tighten_only` is what refuses."""
    base = set(regime(policy, profile)["hard_gates"])
    for gate, state in (overrides or {}).items():
        if gate in GATES and state == "hard":
            base.add(gate)
    return [g for g in GATES if g in base]


def tighten_only(policy: dict, profile: dict, components: int = 0,
                 overrides: dict | None = None) -> list[str]:
    """Every dial the run configuration tries to LOOSEN, named with the file
    that would have to change instead.

    This is the item's centre. A run may always be stricter than the
    constitution; it may never be laxer, and finding out at merge time that a
    profile quietly disabled the review gate is exactly the discovery this
    prevents.
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

    Two channels, and they are independent by construction: the character rule
    above is pure stdlib and always runs; `profile_crosscheck` asks PyYAML,
    which may not be installed, and its own failure to read the file is a
    STATED LIMIT rather than a refusal (`profile_limits`).
    """
    name, text, lines = _profile_source(target)
    if text is None:
        return []
    if problem := _indent_problem(lines, name):
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
    PyYAML about a space would be inventing a second grammar. A top-level `- `
    entry names nothing, for the reason `parse` gives when it refuses one: a
    list at the top level has no key.
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
    """
    block = ""
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not _indented(line):
            body = line.strip()
            key, sep, _ = body.partition(":")
            block = "" if (not sep or body.startswith("- ")) else key.strip()
        yield index, block, line


def _profile_block(target: Path, name: str,
                   lines: list[str] | None = None) -> list[str]:
    """Every line INSIDE the top-level `name:` block, comment-stripped.

    The header is excluded by construction: it is the one line of its block
    that is not indented.

    `lines` is injectable for the reason `runner_for` already discovered - a
    caller that wants two blocks should read the file once, and the two
    functions that used to read it for themselves are exactly the two that
    disagreed with everyone else about what indentation is.
    """
    rows = _profile_lines(target) if lines is None else lines
    return [line for block, line in _profile_sections(rows)
            if block == name and _indented(line)]


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
            if block and not _indented(line)]


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

    **Top-level scalars and lists only, and that boundary is deliberate.**
    Comparing `components:` means comparing a sequence of maps, which is a
    second implementation of the reader rather than a check on it; the two
    defects that live in there (a key dropped after a `vcs:` sub-block, a
    block-form list read as the empty string) are covered by tests instead.
    **The trigger for widening it is a third disagreement inside a sequence
    block** - at which point the comparison is worth its cost, and the cost is
    known: PyYAML's parse is already done and memoised by the caller, so this
    adds a comparison and not a read.
    """
    said = []
    for key in sorted(set(reference) & set(profile_blocks(lines))):
        theirs = reference[key]
        if isinstance(theirs, (str, int, float, bool)):
            ours = scalar_from_lines(lines, key)
            if ours is not None and ours != _same_text(theirs):
                said.append(f"{key} reads {ours!r} here and "
                            f"{_same_text(theirs)!r} to PyYAML")
            elif ours is None:
                said.append(f"{key} has a value to PyYAML "
                            f"({_same_text(theirs)!r}) and none here")
        elif isinstance(theirs, list) and all(
                isinstance(v, (str, int, float, bool)) for v in theirs):
            ours = list_from_lines(lines, key)
            if ours is not None and [str(v) for v in ours] != [
                    _same_text(v) for v in theirs]:
                said.append(f"{key} reads {ours!r} here and {theirs!r} to PyYAML")
    if not said:
        return ""
    return (f"{name}: the standard-library reader and PyYAML disagree about what "
            f"a key SAYS - {'; '.join(said)}. A value one reader cannot see is a "
            f"setting the other will enforce")


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


def profile_dials(target: Path) -> dict:
    """The three dials `tighten_only` judges, read out of `.sdlc/profile.yml`
    with the one stdlib reader this module now owns."""
    dials: dict = {}
    for key in ("gate_profile", "signing", "coverage_enforcement"):
        if (value := profile_top_value(target, key)) is not None:
            dials[key] = value
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


def protected(policy: dict, root: Path, paths: list) -> list[str]:
    """Every write to a file only the CLIs may touch."""
    out = []
    for raw in paths:
        rel = _rel(Path(root), Path(raw)) or str(raw).replace("\\", "/")
        if matches(rel, policy["protected_paths"]):
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

#: Wrappers that are not commands and hold one: a subshell group `( … )`,
#: a command substitution `$( … )`, and a backtick pair. Each is replaced by a
#: separator BEFORE the line is cut up, so what runs inside becomes a command of
#: its own. Measured on the shipped constitution: `( rm -rf build )` tokenised
#: to `['(', 'rm', '-rf', 'build', ')']`, whose program is `(` — a name no rule
#: is about — and `echo $(rm -rf build)` to `['echo', '$(rm', …]`, whose program
#: is `echo`. A parenthesis was a hat that hid `rm` from a wall which could
#: already see through `sudo`.
#:
#: A QUOTE is deliberately not here, and this is the one line to get right. The
#: tokeniser below does not respect quoting: `echo "rm -rf x"` becomes
#: `['echo', 'rm', '-rf', 'x']`, allowed only because the PROGRAM is `echo`.
#: Strip quotes here too and that command is refused for printing a sentence.
#: The consequence, stated rather than assumed: a parenthesis inside a quoted
#: string (`echo "(rm -rf x)"`) IS judged, and refused. Between a refusal
#: somebody argues with and a miss nobody notices, this file has always picked
#: the refusal.
_GROUPING = re.compile(r"\$\(|[()`]")

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
#: `stdbuf` is here for its `-oL`-style flags alone.
_PREFIXES = {"sudo": 0, "doas": 0, "env": 0, "command": 0, "nohup": 0,
             "time": 0, "exec": 0, "timeout": 1, "stdbuf": 0}
_PREFIX_VALUE_FLAGS = {"timeout": ("-s", "--signal", "-k", "--kill-after")}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _program(token: str) -> tuple[str, str]:
    """A command's name and its FAMILY, without directory, extension or case.

    `System32\\cmd.exe`, `/bin/rm` and `RM` are the same program to a wall, and
    a rule that only knew one spelling would be a rule an agent steps around by
    typing the path.

    The family is the name cut at its first dot, because `mkfs.ext4`,
    `mkfs.xfs` and `mkfs.btrfs` are one tool with a filesystem on the end —
    measured, not assumed: `mkfs.ext4 /dev/sda1` walked straight past a rule
    named `mkfs` until this existed.
    """
    name = token.replace("\\", "/").rsplit("/", 1)[-1].strip("\"'").lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".sh"):
        name = name.removesuffix(suffix)
    return name, name.split(".", 1)[0]


def _segment_words(segment: str) -> list[str]:
    """One segment's tokens, unquoted, with the prefixes that are not the
    command dropped.

    Quotes come off because a nested command arrives wearing them:
    `powershell -Command "Remove-Item -Recurse x"` tokenises to `"Remove-Item`,
    which equals nothing a rule can name.

    A prefix that takes arguments takes them with it — see `_PREFIXES`.
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
        positionals = _PREFIXES[name]
        words.pop(0)
        while words and words[0].startswith("-"):
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
#: this, `cmd /c rmdir /s /q x` is a command whose program is `cmd`.
_INTERPRETERS = ("cmd", "powershell", "pwsh", "sh", "bash", "zsh", "dash", "ksh")
_RUN_FLAGS = ("/c", "/k", "-c", "-command", "-comman", "-comm", "-com")


def _commands_in(segment: str) -> list[list[str]]:
    """Every command a single segment actually runs: itself, and anything it
    hands to another program to run."""
    words = _segment_words(segment)
    if not words:
        return []
    found = [words]
    nested_from = None
    if _program(words[0])[0] in _INTERPRETERS:
        for i, word in enumerate(words[1:], 1):
            if word.lower() in _RUN_FLAGS and i + 1 < len(words):
                nested_from = i + 1
                break
    if nested_from is None:
        for i, word in enumerate(words):
            if word.lower() in _EXEC and i + 1 < len(words):
                nested_from = i + 1
                break
    if nested_from is None and _program(words[0])[0] == "xargs":
        nested_from = _xargs_command_at(words)
    if nested_from is not None:
        found.extend(_commands_in(" ".join(words[nested_from:])))
    return found


def _short_letters(words: list[str]) -> set[str]:
    """Every letter appearing in a short-flag CLUSTER.

    `-rf`, `-fr`, `-Rf` and `-r -f` are one operation spelled four ways, and a
    matcher that cannot see that is a matcher an agent defeats by reordering two
    characters. Case-folded, so `-R` counts as `r`.
    """
    out: set[str] = set()
    for word in words:
        if word.startswith("-") and not word.startswith("--") and len(word) > 1 \
                and word[1:].isalpha():
            out.update(word[1:].lower())
    return out


def _requirement_met(alternatives: str, words: list[str], letters: set[str]) -> bool:
    """Whether any spelling of one requirement is present in this segment."""
    for alt in alternatives.split("|"):
        alt = alt.strip().lower()
        if not alt:
            continue
        if len(alt) == 2 and alt.startswith("-") and alt[1].isalpha():
            if alt[1] in letters:            # a short flag, cluster-aware
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
    for segment in _SEPARATORS.split(_GROUPING.sub(";", str(command))):
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
