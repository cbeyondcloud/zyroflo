"""JSON Schema for the governing documents — item 107, the wizard's contract.

**The owner ruled on 2026-08-30 that configs STAY YAML and the wizard's contract
is a SCHEMA, not a storage format.** The template comments are the teaching
layer, the restricted stdlib reader plus the PyYAML crosscheck already de-fang
YAML's foot-guns, and thousands of tests carry YAML fixtures. What a future UI
wizard needs is not a different file format but a machine-readable statement of
what a valid document looks like.

**Every schema here is DERIVED, and that is the whole design.** A hand-written
one would be the fourth parallel statement of a shape this repository already
states three ways — the shipped reader, the pydantic mirror in the suite, and
the template an adopter fills in — and it would be the first that nothing holds
current. So the enums come from `policy.GATE_PROFILES` and its siblings, the
property sets come from `DEFAULTS` and `COMPONENT_KEYS`, the phase list comes
from `stage.NAMES`, and the release dials come from `release.CUT_STEPS`. Add a
constant and the schema grows; rename one and `--check` fails by name.

**The drift guard is `requirements.txt`'s.** The schemas are committed so a
wizard can read them without running anything, and `--check` regenerates and
compares. A generated file nobody diffs is exactly how that export came to hand
people a `tree-sitter` version this repository had never tested with.

**Descriptions POINT at the template rather than copying it.** The teaching
layer is paragraphs now — items 118, 119 and 124 each added one — and lifting a
paragraph onto a key means guessing which key it teaches, at the places where
the teaching matters most. One home, and the schema cites it, which is the rule
the SOPs already follow.

**Not installed into an adopter's repository.** Its live consumer is the drift
guard; a wizard is external and reads the harness. A file installed everywhere
and read nowhere is the dead-config class this project has found seven times.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import models  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402

cli.use_utf8()

#: Where the committed schemas live. Beside the templates they describe, and
#: NOT in the list `sdlc-init.py` installs - see the module docstring.
SCHEMA_DIR = Path("sdlc_templates") / "schemas"

#: The one place the harness's own schema id prefix is written.
BASE_ID = "https://github.com/sumitdevgupto/sdlc-harness/schemas"

HARNESS = Path(__file__).resolve().parents[2]

#: Which template teaches each document. The description of every property
#: points here rather than restating it.
TEACHES = {
    "policy": "sdlc_templates/policy.yml",
    "profile": "sdlc_templates/profile.yml",
    "workspace": "sdlc_templates/workspace.example.toml",
    "events": "tools/sdlc/events.py",
}


class SchemaError(RuntimeError):
    """A schema that cannot be built, or a committed one that has drifted."""


def _json_type(value: object) -> dict:
    """The JSON type of a default, so a property's type is DERIVED from the
    value the harness actually ships rather than transcribed beside it.

    `bool` is checked before `int` deliberately: in Python a bool IS an int, and
    reading `require_conformance` as an integer would let `2` past a schema the
    reader refuses.
    """
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": {"type": "string"}}
    if isinstance(value, dict):
        return {"type": "object"}
    raise SchemaError(f"no JSON type for a default of {type(value).__name__}")


def _cites(document: str, key: str) -> str:
    return f"See {TEACHES[document]} — the key `{key}` is documented there."


#: Policy keys whose values are drawn from a shipped enum, and the constant
#: each one comes from. Keyed by NAME so a renamed constant fails here rather
#: than silently emitting a schema with no enum on it.
POLICY_ENUMS = {
    "gate_profile_floor": "GATE_PROFILES",
    "signing_floor": "SIGNING_MODES",
    "self_approval": "SELF_APPROVAL",
    "coverage_enforcement_floor": "ENFORCEMENT_MODES",
    "identity": "IDENTITY_MODES",
}

#: Policy keys that are LISTS drawn from an enum, same rule one level in.
POLICY_LIST_ENUMS = {"block_on_floor": "SEVERITIES"}

#: Every character `str.strip()` removes - what the readers call padding - by code
#: point, so a pattern can name them with no backslash (T558). A test holds this
#: to `str.isspace` over all of Unicode, so it cannot drift from the reader.
WHITESPACE = "".join(map(chr, (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F,
                               0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B),
                               0x2028, 0x2029, 0x202F, 0x205F, 0x3000)))

#: Constraints `policy.validate` enforces that a DEFAULT cannot express. A
#: default says what the type is; it cannot say that a blank one is refused.
#:
#: **These are stated once here and held by the agreement tests**, which run
#: this schema over `validate`'s own twelve accepted and nineteen rejected
#: documents. The first cut omitted both and the schema came out LOOSER than
#: the reader - the dangerous direction, because a wizard obeying it would emit
#: a document the harness refuses. That test found them; it did not have to be
#: guessed at.
POLICY_CONSTRAINTS = {
    # "an empty rule denies nothing - remove it, or name the program it is
    # about". A whitespace-only rule sits in the constitution looking like a
    # wall, and `min_length` alone accepts "  ".
    "deny_commands": {"items": {"type": "string", "pattern": r"\S"}},
    # "every branch would then be a legal push target, which is the opposite of
    # the rule".
    "push_branch_prefix": {"minLength": 1},
    # T454: "it must be REPO-RELATIVE" - not POSIX-absolute, not a Windows drive
    # or UNC path, never climbing out with `..`. Lookaheads, which JSON Schema's
    # ECMA-262 regex has (pydantic's engine does not, so the suite's mirror uses
    # a validator for the same rule).
    # NO backslash anywhere in this pattern: JSON writes any backslash doubled,
    # and a doubled one in a committed file reads to the leak guard as the start
    # of a UNC path. So `..` is `[.][.]` and the separator is `/` alone - a
    # backslash-rooted path is a DOCUMENTED RESIDUAL here, refused by the reader
    # (`policy.validate`) and the suite's mirror, the way `alias`'s `a != b` is.
    "metrics_out": {"pattern": r"^(?!/)(?![A-Za-z]:)(?!(.*/)?[.][.](/|$))"},
    # T497 and review B5-7: "one directory path inside the repository, spelled
    # with /" - no padding, not absolute, no drive, and no segment that is empty,
    # `.` or `..` (which also refuses blank, the root and a trailing slash). Same
    # lookaheads and no-backslash rule as metrics_out, and the same residual: a
    # backslash-spelled path is refused by the reader and the suite's mirror.
    # T558: "padding" is every character the reader strips, not the space alone -
    # a tab passed here and was refused there. Written as the characters
    # themselves (`WHITESPACE`), which JSON escapes one backslash each; `\s` would
    # be the doubled backslash this rule exists to keep out. "Any character" is
    # the class and its complement, never `.` or the class: `.` takes a space too,
    # and that ambiguity cost 2^k paths for k spaces - 21 s at 28 characters, in a
    # file an adopter's editor evaluates (review B13-2).
    "worktrees_root": {"pattern": f"^(?![{WHITESPACE}])(?!(?:[^{WHITESPACE}]|"
                                  f"[{WHITESPACE}])*[{WHITESPACE}]$)(?!/)(?![A-Za-z]:)"
                                  r"(?!(.*/)?[.]{0,2}(/|$))"},
}


def _enum(name: str) -> list:
    values = getattr(guardrails, name, None)
    if not values:
        raise SchemaError(
            f"policy.{name} is the enum this schema is built from and it is "
            f"absent or empty - the constant moved and nothing else noticed")
    return list(values)


def policy_schema() -> dict:
    """The constitution's shape, from `DEFAULTS` and the enums beside it."""
    props: dict = {}
    for key, default in sorted(guardrails.DEFAULTS.items()):
        if key == "schema_version":
            prop = {"const": guardrails.SCHEMA_VERSION}
        elif key in POLICY_ENUMS:
            prop = {"type": "string", "enum": _enum(POLICY_ENUMS[key])}
        elif key in POLICY_LIST_ENUMS:
            prop = {"type": "array", "minItems": 1,
                    "items": {"type": "string",
                              "enum": _enum(POLICY_LIST_ENUMS[key])}}
        elif key == "push_remote":
            # Item 37 widened this: one name, or a list for an estate whose
            # components publish to different places.
            prop = {"anyOf": [
                {"type": "string", "minLength": 1},
                {"type": "array", "minItems": 1,
                 "items": {"type": "string", "minLength": 1}}]}
        else:
            prop = dict(_json_type(default))
            if isinstance(default, int) and not isinstance(default, bool):
                prop["minimum"] = 0
        prop.update(POLICY_CONSTRAINTS.get(key, {}))
        prop["default"] = default
        prop["description"] = _cites("policy", key)
        props[key] = prop
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_ID}/policy.schema.json",
        "title": "SDLC harness — policy.yml",
        "description": (
            "The repository's SDLC constitution: the LOOSEST each dial may ever "
            "be. Run scope may only tighten against it. "
            + _cites("policy", "every key")),
        "type": "object",
        "additionalProperties": False,
        "properties": props,
    }


#: Profile keys whose values come from a shipped enum.
PROFILE_ENUMS = {
    "gate_profile": "GATE_PROFILES",
    "signing": "SIGNING_MODES",
}

#: Profile keys that are free-form command strings. Named rather than inferred:
#: a key that merely LOOKS like a command is not one, and the harness executes
#: what it finds here.
PROFILE_COMMANDS = ("test_command", "coverage_command", "lint_command",
                    "build_command", "deploy_command")

#: Profile keys that are lists of declared names, each with the module that
#: owns the key. Asked for rather than spelled, which is item 94's rule.
def _quarantine_keys() -> dict:
    from tools.review import sca
    from tools.sdlc import learning, suite
    return {suite.KNOWN_FAILURES: "tools/sdlc/suite.py",
            sca.QUARANTINE_KEY: "tools/review/sca.py",
            learning.SUPPRESS_KEY: "tools/sdlc/learning.py"}


def _release_block() -> dict:
    """The `release:` block, from the steps `release.cut` actually runs."""
    from tools.sdlc import release

    dials = [f"{step}_command" for step in release.CUT_STEPS]
    dials.append(release.ARTIFACT_VERSION_DIAL)
    props = {d: {"type": "string", "description": _cites("profile", d)}
             for d in dials}
    props["notes_from_run"] = {"type": "boolean", "default": True,
                               "description": _cites("profile", "notes_from_run")}
    return {"type": "object", "additionalProperties": False,
            "properties": props,
            "description": _cites("profile", "release")}


def profile_schema() -> dict:
    """The one document an adopter fills in, from the constants that read it."""
    from tools.review import loop

    props: dict = {
        "schema_version": {"const": 2,
                           "description": _cites("profile", "schema_version")},
        "scope": {"type": "string", "enum": ["repo", "estate"],
                  "description": _cites("profile", "scope")},
        "phases": {"type": "array", "description": _cites("profile", "phases"),
                   "items": {"type": "string", "enum": list(stages.NAMES)}},
        # T441c: a profile ADDS a hard human stop at the end of a named step.
        # Same value space as `phases` — a list of step NAMES — since a gate is
        # placed by naming the step it stands after (id `G-<step>`).
        # T512: at the TOP level, where the installer writes it (a first attempt put it
        # in the release block, which nothing reads it from - B9-15).
        "workspace_root": {
            "type": "string",
            "description": (
                "Where the workspace root sits, relative to the directory the harness was "
                "installed into: `repos`, `..`. Every `components[].path` is relative to "
                "that root. Written by the installer above `components:` only when it is "
                "not `.`, with a trailing comment saying so, rather than taught in "
                + TEACHES["profile"] + " - there it would change every profile. Must be "
                "relative: the profile is committed.")},
        # T582: written INSTEAD of `workspace_root` when no relative path reaches the root.
        "workspace_root_unreachable": {
            "type": "string",
            "description": (
                "Why no relative path reaches the workspace root from the directory the "
                "harness was installed into - `another drive or share`. Written by the "
                "installer in place of `workspace_root`, with a trailing comment saying so, "
                "and for the same reason not taught in " + TEACHES["profile"] + ". While it "
                "stands, every run step that opens a component refuses with this reason; put "
                "the two on one drive or share and re-run the installer, which names the "
                "change this line needs.")},
        "extra_gates": {"type": "array",
                        "description": _cites("profile", "extra_gates"),
                        "items": {"type": "string", "enum": list(stages.NAMES)}},
        "components": {
            "type": "array", "description": _cites("profile", "components"),
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    **{"name": {"type": "string", "minLength": 1,
                                "description": _cites("profile", "name")}},
                    # Item 130. These were ALL typed `string`, including the
                    # three the top level of this same schema types `array` -
                    # so the derived schema rejected every multi-component
                    # profile the installer writes, `render_components` putting
                    # `known_failures: []` into each one. It survived because
                    # the template test replaces `@@COMPONENTS@@` with "" and
                    # so never validates a component at all. Derived from
                    # `policy.COMPONENT_LIST_KEYS`, which item 130 created for
                    # the reader, rather than from a fourth hand-written list.
                    # Item 264: a component's keys are properties too, so each
                    # cites the template the way every top-level key does.
                    **{k: ({"type": "array", "items": {"type": "string"},
                            "description": _cites("profile", k)}
                           if k in guardrails.COMPONENT_LIST_KEYS
                           else {"type": "string",
                                 "description": _cites("profile", k)})
                       for k in guardrails.COMPONENT_KEYS
                       if k != "coverage_threshold"},
                    "coverage_threshold": {
                        "type": "integer", "minimum": 0, "maximum": 100,
                        "description": _cites("profile", "coverage_threshold")},
                    "vcs": {"type": "object",
                            "description": _cites("profile", "vcs")},
                }}},
        "release": _release_block(),
        "review": {
            "type": "object", "description": _cites("profile", "review"),
            "properties": {
                "block_on": {"type": "array",
                             "items": {"type": "string",
                                       "enum": list(loop.SEVERITIES)}},
                "max_iterations": {"type": "integer", "minimum": 1}}},
        # T496: `deny_raw_git` is the one vcs value that is not text - the
        # installer writes `deny_raw_git: false` and the reader reads a boolean -
        # so typing it `string` refused every profile the installer writes.
        "vcs": {"type": "object", "description": _cites("profile", "vcs"),
                "properties": {k: ({"type": "boolean"} if k == "deny_raw_git"
                                   else {"type": "string"})
                               for k in guardrails.VCS_KEYS}},
        # T442: the pre-INIT metadata, rendered by the installer and read by
        # `policy.profile_project`. Keys derived from that reader's own tuple.
        "project": {"type": "object", "description": _cites("profile", "project"),
                    "properties": {k: ({"type": "array", "items": {"type": "string"}}
                                       if k == "compliance" else {"type": "string"})
                                   for k in guardrails.PROJECT_KEYS}},
        # T445a: whether the harness calls models itself, and which. The step
        # keys come from the registry; the value shape is `models._SPEC`'s.
        "model_routing": {"type": "boolean",
                          "description": _cites("profile", "model_routing")},
        "models": {"type": "object", "description": _cites("profile", "models"),
                   "additionalProperties": False,
                   "properties": {k: {"type": "string", "pattern": models._SPEC.pattern}
                                  for k in (models.DEFAULT_KEY, *stages.NAMES)}},
        # T244 round 2: a multi-unit repository installed with --single-component
        # records the assertion HERE, structured, bounded to the unit count it was
        # made over - not as a substring of free text, which is what let a comment
        # merely mentioning the flag pass for an assertion (review W376).
        "single_component": {
            "type": "object",
            "description": _cites("profile", "single_component"),
            "properties": {
                "asserted": {"type": "boolean"},
                "units": {"type": "integer", "minimum": 0},
            },
        },
        "probes": {"type": "array", "items": {"type": "string"},
                   "description": _cites("profile", "probes")},
        "coverage_threshold": {"type": "integer", "minimum": 0, "maximum": 100,
                               "description": _cites("profile",
                                                     "coverage_threshold")},
        "coverage_scope": {"type": "string", "enum": ["changed", "whole"],
                           "description": _cites("profile", "coverage_scope")},
        # T496: a runner's `phases` is its self-scope, a LIST of step names
        # (`runner_for` reads it as one, and the template teaches
        # `phases: [implementation]`). No enum: the reader does not refuse an
        # unknown name there, and a schema stricter than its reader disagrees.
        "runners": {
            "type": "array", "description": _cites("profile", "runners"),
            "items": {"type": "object", "required": ["name", "command"],
                      "properties": {k: ({"type": "array", "items": {"type": "string"}}
                                         if k == "phases" else {"type": "string"})
                                     for k in guardrails.RUNNER_KEYS}}},
        "phase_runners": {"type": "object",
                          "description": _cites("profile", "phase_runners")},
        "gates": {"type": "object", "description": _cites("profile", "gates"),
                  "properties": {g: {"type": "string",
                                     "enum": list(guardrails.GATE_STATES)}
                                 for g in guardrails.GATES}},
    }
    for key, enum_name in PROFILE_ENUMS.items():
        props[key] = {"type": "string", "enum": _enum(enum_name),
                      "description": _cites("profile", key)}
    for key in PROFILE_COMMANDS:
        props[key] = {"type": "string", "description": _cites("profile", key)}
    for key, owner in _quarantine_keys().items():
        props[key] = {"type": "array", "items": {"type": "string"},
                      "description": _cites("profile", key)
                      + f" Owned by {owner}."}
    for key in ("stack", "stack_note", "plans_dir", "conventions_files",
                "tools", "stacks"):
        props[key] = ({"type": "array", "items": {"type": "string"}}
                      if key in ("stacks", "conventions_files", "tools")
                      else {"type": "string"})
        props[key]["description"] = _cites("profile", key)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_ID}/profile.schema.json",
        "title": "SDLC harness — .sdlc/profile.yml",
        "description": (
            "The ONE file that makes the generic harness fit a workspace. "
            + _cites("profile", "every key")),
        "type": "object",
        # NOT `additionalProperties: false`. The profile is the document an
        # adopter EXTENDS - the installer writes keys per stack, and refusing an
        # unknown one would make this schema a stricter gate than the reader it
        # describes, which is a wizard refusing a profile the harness accepts.
        "additionalProperties": True,
        "properties": props,
    }


def _installer():
    """Load the stdlib-only installer for the workspace reader's OWN constants -
    `WORKSPACE_SCHEMA_VERSION`, `COMPONENT_ROLES` and the key sets `read_workspace`
    validates against. The workspace schema is derived from these the way the
    policy schema is derived from `DEFAULTS` (item 259), so a key added to the
    reader grows the schema and a renamed one fails `--check` by name. Loaded the
    way build.py and the workspace tests reach it - the hyphen bars a plain import
    - and cached, so it is read once. Stdlib-only by the installer's own gate, so
    this adds no third-party import to `tools/`."""
    import importlib.util
    if "sdlc_init" in sys.modules:
        return sys.modules["sdlc_init"]
    spec = importlib.util.spec_from_file_location("sdlc_init", HARNESS / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def workspace_schema() -> dict:
    """The pre-INIT workspace declaration. TOML rather than YAML, and the only
    one of the three an adopter writes BEFORE the harness exists in their
    repository - which is why its shape is the installer's to state.

    **DERIVED from `read_workspace`, not transcribed.** The top-level keys are its
    `_WS_TOP_KEYS`, the component keys its `_WS_COMPONENT_KEYS`, the version its
    `WORKSPACE_SCHEMA_VERSION` and the roles its `COMPONENT_ROLES`. The hand-typed
    version this replaced had drifted three ways (item 259): it omitted
    `schema_version` and `profile`, and it named the component list `components`
    where the reader and the TOML template both write `component`, so that one
    property described no real document. The reader refuses an unknown key, so
    `additionalProperties` is False here where the profile's is True - and the
    completeness is held by `test_the_workspace_schema_covers_every_key_the_reader_knows`."""
    si = _installer()

    # The reader `.strip()`s every value before its non-blank test, so `\S` (a
    # non-whitespace char must be present), not `minLength: 1`, is what actually
    # matches it - `minLength` would accept "   " the reader refuses. Same idiom
    # `POLICY_CONSTRAINTS` uses for `deny_commands`.
    NONBLANK = {"pattern": r"\S"}
    # A graph node id is `TYPE:REST`; the reader refuses one without a colon
    # (blank included - a blank cannot carry one).
    HAS_COLON = {"pattern": ":"}

    def string(key: str, **extra) -> dict:
        return {"type": "string", "description": _cites("workspace", key), **extra}

    def string_list(key: str) -> dict:
        return {"type": "array", "items": {"type": "string"},
                "description": _cites("workspace", key)}

    def block(keys, required, constraints=None) -> dict:
        """A [[block]]'s item shape: every documented key, the reader's own strict
        unknown-key refusal, and the value constraint it enforces per key."""
        constraints = constraints or {}
        return {"type": "object", "required": sorted(required),
                "additionalProperties": False,
                "properties": {k: string(k, **constraints.get(k, {}))
                               for k in sorted(keys)}}

    def component_prop(key: str) -> dict:
        """A component's keys carry the varied types the reader enforces - a role
        off the enum, an out-of-range threshold and a bare-string list are each
        refused by `read_workspace` - so the schema states them, not just `string`."""
        if key == "role":
            return {"type": "string", "enum": list(si.COMPONENT_ROLES),
                    "description": _cites("workspace", "role")}
        if key == "coverage_threshold":
            return {"type": "integer", "minimum": 0, "maximum": 100,
                    "description": _cites("workspace", "coverage_threshold")}
        if key == "known_failures":
            return string_list(key)
        if key == "name":
            # A component with a blank name is refused (`_ws_str` + `if not name`),
            # and the name is what an override attaches to.
            return string(key, **NONBLANK)
        if key == "vcs":
            # The reader validates the vcs sub-keys against `_WS_VCS_KEYS` and
            # refuses an unknown one, so the schema enumerates them behind the same
            # closed door. It does NOT type the values, because the reader does not
            # either - a `deny_raw_git` bool sits beside string commands.
            return {"type": "object", "additionalProperties": False,
                    "description": _cites("workspace", "vcs"),
                    "properties": {k: {"description": _cites("workspace", k)}
                                   for k in sorted(si._WS_VCS_KEYS)}}
        return string(key)

    props = {
        "schema_version": {"const": si.WORKSPACE_SCHEMA_VERSION,
                           "description": _cites("workspace", "schema_version")},
        "root": string("root", **NONBLANK),
        "name": string("name"),
        "profile": string("profile"),
        "include": string_list("include"),
        "exclude": string_list("exclude"),
        "component": {
            "type": "array", "description": _cites("workspace", "component"),
            "items": {"type": "object", "required": ["name"],
                      "additionalProperties": False,
                      "properties": {k: component_prop(k)
                                     for k in sorted(si._WS_COMPONENT_KEYS)}}},
        "canonical": {
            "type": "array", "description": _cites("workspace", "canonical"),
            "items": block(si._WS_CANONICAL_KEYS, ("owner", "service"),
                           {"service": NONBLANK, "owner": NONBLANK})},
        "generated": {
            "type": "array", "description": _cites("workspace", "generated"),
            # `path` is `.strip("/")`-ed after whitespace, so a slash-only value
            # collapses to blank; require a real segment character, not just \S.
            "items": block(si._WS_GENERATED_KEYS, ("component", "path"),
                           {"component": NONBLANK, "path": {"pattern": r"[^/\s]"}})},
        "entrypoint": {
            "type": "array", "description": _cites("workspace", "entrypoint"),
            "items": block(si._WS_ENTRYPOINT_KEYS, ("node",), {"node": HAS_COLON})},
        # T442: an optional `[project]` table - the pre-INIT metadata, when the
        # workspace carries it rather than the target's sdlc-project.toml. A
        # blank required field is valid HERE (stop-and-fill names it at INIT).
        "project": {
            "type": "object", "description": _cites("workspace", "project"),
            "additionalProperties": False,
            "properties": {k: ({"type": "array", "items": {"type": "string"}}
                               if k == "compliance"
                               else {"type": "string", "enum": ["", *si.CRITICALITY]}
                               if k == "criticality" else {"type": "string"})
                           for k in sorted(si._PROJECT_KEYS)}},
        "alias": {
            "type": "array", "description": _cites("workspace", "alias"),
            # `a != b` is a cross-field inequality JSON Schema cannot state; it is
            # a documented residual, refused by the reader, left out of the corpus.
            "items": block(si._WS_ALIAS_KEYS, ("a", "b"),
                           {"a": HAS_COLON, "b": HAS_COLON})},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_ID}/workspace.schema.json",
        "title": "SDLC harness — workspace.toml",
        "description": ("A multi-repo workspace, declared before INIT runs. "
                        + _cites("workspace", "every key")),
        "type": "object",
        "required": ["root"],
        # The reader refuses an unknown top-level key (`_ws_reject`), so the schema
        # does too - unlike the profile, which an adopter deliberately extends.
        "additionalProperties": False,
        "properties": props,
    }


#: Every document this module states, and the function that derives it.
def events_schema() -> dict:
    """One line of a run's `events.jsonl` (T446a), DERIVED from `events`: the
    type enum, the common fields every event carries, and which extra fields
    each type may carry - so a dashboard reading the file reads a contract,
    and a field the writer adds without declaring fails the build by name."""
    from tools.sdlc import events

    every = (*events.COMMON, *sorted({f for fs in events.FIELDS.values() for f in fs}))
    missing = [f for f in every if f not in events.FIELD_TYPES]
    if missing:
        raise SchemaError(f"events fields with no declared type: {', '.join(missing)}")
    props = {f: {"type": events.FIELD_TYPES[f], "description": _cites("events", f)}
             for f in every}
    props["event"] = {"enum": list(events.TYPES), "description": _cites("events", "event")}
    props["schema_version"] = {"const": events.SCHEMA_VERSION,
                               "description": _cites("events", "schema_version")}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_ID}/events.schema.json",
        "title": "SDLC harness — events.jsonl (one line)",
        "description": ("One event in a run's append-only, hash-chained audit trail. "
                        + _cites("events", "every field")),
        "type": "object",
        "required": list(events.COMMON),
        "additionalProperties": False,
        "properties": props,
        "allOf": [{"if": {"properties": {"event": {"const": t}}},
                   "then": {"propertyNames": {"enum": [*events.COMMON, *events.FIELDS[t]]}}}
                  for t in events.TYPES],
    }


BUILDERS = {
    "policy": policy_schema,
    "profile": profile_schema,
    "workspace": workspace_schema,
    "events": events_schema,
}


def rendered(name: str) -> str:
    """One schema, as the bytes the committed file must carry."""
    if name not in BUILDERS:
        raise SchemaError(f"unknown document {name!r} - one of "
                          f"{', '.join(sorted(BUILDERS))}")
    return json.dumps(BUILDERS[name](), indent=2, sort_keys=True) + "\n"


def path_of(name: str, harness: Path | None = None) -> Path:
    return (harness or HARNESS) / SCHEMA_DIR / f"{name}.schema.json"


def write(harness: Path | None = None) -> list[str]:
    """Regenerate every committed schema. The maintainer's act."""
    done = []
    for name in sorted(BUILDERS):
        out = path_of(name, harness)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered(name), encoding="utf-8", newline="\n")
        done.append(str(out))
    return done


def drift(harness: Path | None = None) -> list[str]:
    """Where a committed schema disagrees with what the constants say today.

    **Named by document and by the first differing line**, because "the schema
    is stale" is not something anybody can act on. `requirements.txt` earned
    that wording: its header had said *regenerate me* since item 68 and it
    drifted anyway, silently, for exactly the people with no lock to fall back
    on.
    """
    said = []
    for name in sorted(BUILDERS):
        out = path_of(name, harness)
        want = rendered(name)
        if not out.is_file():
            said.append(f"{name}: {out} is not committed - run "
                        f"`tools/sdlc/schema.py --write`")
            continue
        have = out.read_text(encoding="utf-8")
        if have == want:
            continue
        w, h = want.splitlines(), have.splitlines()
        first = next((i for i in range(max(len(w), len(h)))
                      if w[i:i + 1] != h[i:i + 1]), 0)
        # Each side is shown VERBATIM via `!r`, never stripped: when the only
        # difference is whitespace, stripping both prints the same string twice
        # and hides the very thing that differs (item 262). `repr` quotes the
        # line so leading and trailing spaces are visible where they matter.
        said.append(
            f"{name}: {out.name} no longer matches the constants it is derived "
            f"from, first at line {first + 1}: committed "
            f"{(h[first:first + 1] or ['<end of file>'])[0]!r} vs "
            f"derived {(w[first:first + 1] or ['<end of file>'])[0]!r} "
            f"- run `tools/sdlc/schema.py --write`")
    return said


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="regenerate the committed schemas from the constants")
    ap.add_argument("--check", action="store_true",
                    help="fail when a committed schema has drifted (exit 1)")
    ap.add_argument("--print", dest="show", metavar="DOC",
                    help=f"one schema to stdout: {', '.join(sorted(BUILDERS))}")
    args = ap.parse_args(argv)

    if args.show:
        print(rendered(args.show), end="")
        return 0
    if args.write:
        for made in write():
            print(f"wrote {made}")
        return 0
    if args.check:
        said = drift()
        for line in said:
            print(f"!! {line}")
        if not said:
            print(f"{len(BUILDERS)} schema(s) match the constants they are "
                  f"derived from")
        return 1 if said else 0
    ap.print_help()
    return 2


def main(argv: list[str] | None = None) -> int:
    # Exit 2, not 3: this CLI has no could-not-look answer - it either derives
    # the schemas or it cannot, and 1 already means DRIFTED. Naming the code at
    # the call site is the rule `cli.door` states.
    return cli.door(_main, argv, "the schema generator", 2)


if __name__ == "__main__":
    raise SystemExit(main())
