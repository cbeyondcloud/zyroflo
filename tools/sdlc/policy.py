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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import intake as intake_mod  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
POLICY_NAME = "policy.yml"

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
    "protected_paths": [".env", "**/.env", "**/policy.yml", "**/approvals.jsonl",
                        "**/state.json", "**/learning_log.jsonl"],
    "push_remote": "origin",
    "push_branch_prefix": "sdlc/",
    "deny_git": ["--force", "-f origin", "push -f", "reset --hard", "clean -f",
                 "filter-branch", "filter-repo", "branch -D", "update-ref",
                 "reflog expire", "gc --prune"],
    "max_files_per_batch": 40,
    "max_lines_per_batch": 1500,
    "budget_exempt": ["**/*.lock", "**/package-lock.json", "**/uv.lock",
                      "**/poetry.lock", "**/Cargo.lock", "**/go.sum"],
}

#: Where the constitution may live, in the order the resolver looks. `sdlc/` is
#: the plan's spelling for a repository that keeps lifecycle files together;
#: `.sdlc/` is where this harness's own installer writes everything else.
POLICY_LOCATIONS = (Path("sdlc") / POLICY_NAME, Path(".sdlc") / POLICY_NAME)

_COMMENT = re.compile(r"\s#")


class PolicyError(RuntimeError):
    """A policy that could not be read or does not validate. Never downgraded
    to a warning: the caller's next act would be to enforce it."""


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


def _inline_list(text: str) -> list:
    inner = text.strip()[1:-1].strip()
    return [_scalar(p) for p in inner.split(",") if p.strip()] if inner else []


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
            raise PolicyError(f"{name}:{n}: tabs are not indentation in YAML")
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
    for rel in POLICY_LOCATIONS:
        if (target / rel).is_file():
            return target / rel
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
    for key in ("max_files_per_batch", "max_lines_per_batch"):
        if key in doc and (not isinstance(doc[key], int) or isinstance(doc[key], bool)
                           or doc[key] < 0):
            problems.append(f"{key}: {doc[key]!r} - a whole number, or 0 for no limit")
    for key in ("protected_paths", "deny_git", "budget_exempt"):
        if key in doc and not isinstance(doc[key], list):
            problems.append(f"{key} must be a list")
    for key in ("worktrees_root", "push_remote", "push_branch_prefix"):
        if key in doc and not isinstance(doc[key], str):
            problems.append(f"{key} must be text")
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
    path = target / ".sdlc" / "profile.yml"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    inside, count = False, 0
    for line in text.splitlines():
        stripped = _strip_comment(line).rstrip()
        if not stripped.strip():
            continue
        if not stripped.startswith((" ", "\t")):
            inside = stripped.startswith("components:")
            continue
        if inside and stripped.strip().startswith("- name:"):
            count += 1
    return count


def gate_overrides(target: Path) -> dict[str, str]:
    """The profile's per-gate overrides, from a `gates:` block of `G<n>: state`."""
    path = target / ".sdlc" / "profile.yml"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    inside, found = False, {}
    for line in text.splitlines():
        stripped = _strip_comment(line).rstrip()
        if not stripped.strip():
            continue
        if not stripped.startswith((" ", "\t")):
            inside = stripped.startswith("gates:")
            continue
        if inside and ":" in stripped:
            gate, _, state = stripped.strip().partition(":")
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
    if chosen == "solo" and components > 1:
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


def profile_dials(target: Path) -> dict:
    """The three dials `tighten_only` judges, read out of `.sdlc/profile.yml`
    with the stdlib reader intake already owns."""
    dials: dict = {}
    for key in ("gate_profile", "signing"):
        if (value := intake_mod.profile_value(target, key)) is not None:
            dials[key] = value
    path = target / ".sdlc" / "profile.yml"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return dials
    # `block_on` is nested under `review:`; the one nested value this needs, so
    # it is read here rather than by growing intake's top-level-scalar contract.
    for line in text.splitlines():
        stripped = _strip_comment(line).strip()
        if stripped.startswith("block_on:"):
            rest = stripped.partition(":")[2].strip()
            if rest.startswith("[") and rest.endswith("]"):
                dials["block_on"] = [str(v) for v in _inline_list(rest)]
            break
    return dials


# ── what an agent may not do ─────────────────────────────────────────────────


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


def push_target(policy: dict, remote: str, branch: str, run_id: str = "") -> list[str]:
    """Whether this push goes to the one place this run may publish."""
    problems = []
    if remote and remote != policy["push_remote"]:
        problems.append(f"remote {remote!r}: agents push only to "
                        f"{policy['push_remote']!r}")
    prefix = policy["push_branch_prefix"]
    if not branch.startswith(prefix):
        problems.append(f"branch {branch!r}: agents push only to {prefix}<run-id>")
    elif run_id and branch[len(prefix):].strip("/") != run_id:
        problems.append(f"branch {branch!r} belongs to another run, not {run_id}")
    return problems


def destructive(policy: dict, command: str) -> list[str]:
    """Whether a git command destroys history or someone else's work. Whitespace
    is collapsed first, so extra spaces cannot hide a match."""
    flat = " ".join(str(command).split())
    return [f"{command!r}: matches the denied git operation {token!r}"
            for token in policy["deny_git"] if str(token) in flat]


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
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2
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
    except (PolicyError, OSError) as exc:
        print(f"!! {exc}")
        return 2

    source = args.file or (str(locate(target)) if locate(target)
                           else "built-in defaults (no policy.yml in this repository)")
    dials = profile_dials(target)
    overrides = gate_overrides(target)
    components = component_count(target)
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
    if problems:
        print(f"REFUSED - {len(problems)} policy problem(s).")
        return 1
    if args.validate or args.show or not (args.write or args.diff or args.push
                                          or args.git or args.approver):
        print("OK - within policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
