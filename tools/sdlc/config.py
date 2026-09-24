"""`sdlc config` - the configuration wizard's engine: its questions, a change, and a check (T371a).

    sdlc config --target . --spec                        the questions, as JSON
    sdlc config --target . set gate_profile=team         one change (several pairs are ONE change)
    sdlc config --target . apply --from answers.json     the same change, from a JSON object
    sdlc config --target . --check                       would a run accept this configuration?

**Non-interactive on purpose.** An agent's tool call has no TTY, so a wizard
that asks with `input()` hangs or reads EOF exactly where most people use it.
This is the ENGINE. The front ends (T371b) - the conversational `/sdlc-config`
entry point and a terminal loop - ask the questions and apply each answer
through it, so there is one implementation of what a valid answer is.

**The questions are DERIVED, never written out here.** Their types, choices and
patterns come from the committed schemas `schema.py` holds current:
`profile.schema.json` for the pipeline, and `workspace.schema.json`'s
`[project]` table for the metadata `sdlc-project.toml` carries. Which project
fields INIT requires comes from the installer's own `PROJECT_REQUIRED`, and the
current value of every question is read through the harness's own readers.

**It never writes a configuration a run would refuse.** Validation is the
run's, not a second rulebook: the profile reader's refusals, `stage.pipeline`,
the `tighten_only` floors with the resolved phases and hard gates (the call
`approve.record` makes), and `models.model_for` for every active step. The
candidate is checked through `policy.proposed_profile` BEFORE a byte is
written - the one door every profile reader goes through - and each written
key is then read back through those readers, so a value the file format cannot
carry is refused rather than stored as a second story.

**A change is whole or absent.** Several pairs, or an answers file, are one
change: all of it is validated, then every file is replaced atomically, and a
failed write puts back what it had already replaced. Edits are line edits:
every comment stays where it was and the file's line endings are kept.

**Where each answer goes.** The pipeline keys go to `.sdlc/profile.yml`, which
must exist - before INIT they are refused by name. The `project.*` keys go to
`sdlc-project.toml`, written from the template if it is not there yet (INIT's
own stop-and-fill file). Once installed they go to the profile's `project:`
block TOO, because INIT never rewrites a profile somebody edited, and run
records are stamped from that block. `.sdlc/pipeline.md` is redrawn after
every change. A workspace whose TOML carries its own `[project]` table wins at
INIT; edit that one there.

**Exit codes.** 0: applied, or already so, or clean. 1: refused - a run or INIT
would refuse the result, and nothing was written. 2: could not look, or the
input itself is malformed (an unknown key, a value off its schema).
"""
from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import sys
import tempfile
import tomllib
from difflib import get_close_matches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import models  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import schema  # noqa: E402
from tools.sdlc import stage  # noqa: E402

cli.use_utf8()

PROFILE_REL = Path(".sdlc") / guardrails.PROFILE_NAME
DIAGRAM_REL = stage.DIAGRAM_FILE
SPEC_VERSION = 1

#: What each question asks. Prose is the wizard's own; everything a valid
#: answer IS - type, choices, pattern - is read from the committed schemas.
PROMPTS = {
    "project.name": "A short, stable name for this repository.",
    "project.description": "One sentence: what this repository does.",
    "project.owner": "Who answers for it: a team, or a person.",
    "project.criticality": "How much it matters when this breaks.",
    "project.compliance": "Compliance regimes this code falls under, as tags (e.g. sox, pci).",
    "project.default_branch": "The branch changes merge into.",
    "project.issue_tracker": "Where this repository's work items live: a project URL or key.",
    "phases": "Which steps this repository runs. The locked steps cannot be removed, "
              "and the order is the lifecycle's whatever order they are listed in.",
    "gate_profile": "How many human stops the run makes, and how finely agent changes "
                    "are reviewed.",
    "extra_gates": "Add a hard human stop at the end of these steps. Each must be a "
                   "step this repository runs.",
    "gates.<gate>": "Make a built-in gate hard where the gate profile leaves it "
                    "advisory. Never softer: G4, the merge gate, is hard in every regime.",
    "signing": "Sign each approval (dsse), so it can be attributed across people.",
    "review.block_on": "Which review findings block: the review loop runs until none "
                       "of these severities is open.",
    "model_routing": "Should the harness call models itself? false: the AI coding tool "
                     "you use runs every step, with its own model.",
    "models.default": "With model_routing on: the provider/model for a step that names "
                      "none of its own.",
    "models.<step>": "With model_routing on: the provider/model for one step. A chat "
                     "step only - a step that must act needs a runner.",
}

#: The exceptions a reader, the resolver or the model router raise to REFUSE.
_REFUSALS = (stage.StageError, guardrails.PolicyError, guardrails.RunnerError,
             models.ModelError)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ConfigError(RuntimeError):
    """Could not look, or the input itself is malformed: exit 2, nothing written."""


class Refused(RuntimeError):
    """The change would leave a configuration a run or INIT refuses: exit 1,
    nothing written. Carries every reason, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


# ── where the rules live ──────────────────────────────────────────────────────


def _installer():
    """The stdlib installer, for the project file's own rules (`PROJECT_REQUIRED`,
    `read_project`, the template). It lives beside a harness clone, not in the
    wheel, and so do the schemas - hence the one sentence for both."""
    try:
        return schema._installer()
    except Exception as exc:       # noqa: BLE001 - absent, or unloadable: said by name
        raise ConfigError(f"sdlc-init.py could not be loaded ({type(exc).__name__}: "
                          f"{exc}) - sdlc config runs from a clone of the harness, "
                          f"where the installer and sdlc_templates/ live") from None


def _schemas() -> tuple[dict, dict]:
    """The committed profile and workspace schemas. READ, never re-derived here:
    they are the contract, and `schema.py --check` is what holds them current."""
    out = []
    for name in ("profile", "workspace"):
        path = schema.path_of(name)
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"the committed {name} schema could not be read "
                              f"({path.name}: {exc}) - sdlc config runs from a clone "
                              f"of the harness, where sdlc_templates/schemas/ lives") from None
    return out[0], out[1]


# ── the questions ─────────────────────────────────────────────────────────────


def questions() -> list[dict]:
    """Every question, in the order a front end asks them: what the repository
    is, then the pipeline. Two are KEYED - `gates.<gate>` and `models.<step>` -
    and are answered as `gates.G3=hard`, one key at a time."""
    si = _installer()
    profile, workspace = _schemas()
    table = workspace["properties"]["project"]["properties"]
    order = [*si.PROJECT_REQUIRED, *si.PROJECT_OPTIONAL]
    if sorted(order) != sorted(table):
        raise ConfigError("the installer's project fields and the committed workspace "
                          "schema's [project] table disagree - run "
                          "`tools/sdlc/schema.py --check`")
    out: list[dict] = []
    for key in order:
        prop = table[key]
        q = {"id": f"project.{key}", "file": si.PROJECT_FILE,
             "required": key in si.PROJECT_REQUIRED, "prompt": PROMPTS[f"project.{key}"],
             "doc": prop.get("description", "")}
        if prop.get("type") == "array":
            q["type"] = "list"
        elif prop.get("enum"):
            q["type"], q["choices"] = "choice", [c for c in prop["enum"] if c]
        else:
            q["type"] = "text"
        out.append(q)

    props = profile["properties"]

    def ask(qid: str, prop: dict, **extra) -> dict:
        return {"id": qid, "file": PROFILE_REL.as_posix(), "required": False,
                "prompt": PROMPTS[qid], "doc": prop.get("description", ""), **extra}

    out.append(ask("phases", props["phases"], type="multi",
                   choices=list(props["phases"]["items"]["enum"]),
                   locked=list(stage.LOCKED_PHASES), default=list(stage.SOLO_DEFAULT),
                   needs={s.name: s.needs for s in stage.STAGES if s.needs}))
    out.append(ask("gate_profile", props["gate_profile"], type="choice",
                   choices=list(props["gate_profile"]["enum"]),
                   presets={name: {"hard_gates": list(p["hard_gates"]),
                                   "signing": p["signing"],
                                   "block_on": list(p["block_on"])}
                            for name, p in guardrails.PRESETS.items()}))
    out.append(ask("extra_gates", props["extra_gates"], type="multi",
                   choices=list(props["extra_gates"]["items"]["enum"]),
                   subset_of="phases", default=[]))
    gates = props["gates"]["properties"]
    states = {tuple(g["enum"]) for g in gates.values()}
    if len(states) != 1:
        raise ConfigError("the committed profile schema gives the gates different "
                          "states - run `tools/sdlc/schema.py --check`")
    out.append(ask("gates.<gate>", props["gates"], type="choice", keys=sorted(gates),
                   choices=list(states.pop()), clearable=True))
    out.append(ask("signing", props["signing"], type="choice",
                   choices=list(props["signing"]["enum"])))
    # A regime is not only its gates: `regulated` also requires `medium` here,
    # so a wizard that offered the choice without this question could never
    # apply it.
    block_on = props["review"]["properties"]["block_on"]
    out.append(ask("review.block_on", props["review"], type="multi",
                   choices=list(block_on["items"]["enum"])))
    if props["model_routing"].get("type") != "boolean":
        raise ConfigError("the committed profile schema no longer types model_routing "
                          "as a boolean - run `tools/sdlc/schema.py --check`")
    out.append(ask("model_routing", props["model_routing"], type="boolean", default=False))
    specs = props["models"]["properties"]
    out.append(ask("models.default", props["models"], type="text",
                   pattern=specs[models.DEFAULT_KEY]["pattern"], clearable=True))
    out.append(ask("models.<step>", props["models"], type="text",
                   keys=sorted(k for k in specs if k != models.DEFAULT_KEY),
                   pattern=specs[models.DEFAULT_KEY]["pattern"], clearable=True))
    return out


def _installed(target: Path) -> bool:
    return (target / PROFILE_REL).is_file()


def current_values(target: Path) -> tuple[dict, list[str]]:
    """{question id: its value now}, and what could not be read - said, never
    guessed. A keyed question's value is the {key: value} map it holds."""
    si = _installer()
    cur: dict = {}
    unreadable: list[str] = []
    toml = target / si.PROJECT_FILE
    if toml.is_file():
        try:
            cur.update({f"project.{k}": v for k, v in si.read_project(toml).items()})
        except si.WorkspaceError as exc:
            unreadable.append(str(exc))
    if not _installed(target):
        return cur, unreadable
    readers = {
        "phases": lambda: stage.declared(target)[0],
        "gate_profile": lambda: guardrails.profile_top_value(target, "gate_profile"),
        "extra_gates": lambda: [str(v) for v in
                                (guardrails.profile_list(target, "extra_gates") or [])],
        "gates.<gate>": lambda: guardrails.gate_overrides(target),
        "signing": lambda: guardrails.profile_top_value(target, "signing"),
        "review.block_on": lambda: [str(v) for v in
                                    (guardrails.profile_list(target, "block_on") or [])],
        "model_routing": lambda: models.model_routing(target),
        "models": lambda: models.models_declared(target),
        "project": lambda: guardrails.profile_project(target),
    }
    for qid, read in readers.items():
        try:
            value = read()
        except _REFUSALS as exc:
            unreadable.append(f"{qid}: {exc}")
            continue
        if qid == "models":
            cur["models.default"] = value.get(models.DEFAULT_KEY)
            cur["models.<step>"] = {k: v for k, v in value.items()
                                    if k != models.DEFAULT_KEY}
        elif qid == "project":
            # The profile's block answers only where there is no file to ask:
            # an install whose metadata came from a workspace's own table.
            if not toml.is_file():
                cur.update({f"project.{k}": v for k, v in value.items()})
        else:
            cur[qid] = value
    return cur, unreadable


def spec(target: Path) -> dict:
    qs = questions()
    cur, unreadable = current_values(target)
    for q in qs:
        q["current"] = cur.get(q["id"])
    return {"spec_version": SPEC_VERSION, "installed": _installed(target),
            "questions": qs, "unreadable": unreadable,
            "answer_with": "sdlc config set <id>=<value> ..., or "
                           "sdlc config apply --from <answers.json>; a keyed id is "
                           "answered per key, e.g. gates.G3=hard"}


# ── reading an answer ─────────────────────────────────────────────────────────


def _question_for(key: str, qs: list[dict]) -> tuple[dict, str | None]:
    """The question `key` answers, and the gate or step a keyed one names."""
    for q in qs:
        if q["id"] == key and "keys" not in q:
            return q, None
    head, dot, tail = key.partition(".")
    for q in qs:
        if dot and "keys" in q and q["id"].split(".", 1)[0] == head:
            if tail in q["keys"]:
                return q, tail
            raise ConfigError(f"{key}: {head} is keyed by one of {', '.join(q['keys'])}")
    names = [q["id"] for q in qs if "keys" not in q] + [
        f"{q['id'].split('.', 1)[0]}.{k}" for q in qs if "keys" in q for k in q["keys"]]
    hint = get_close_matches(key, names, n=1)
    raise ConfigError(f"unknown key {key!r}" + (f" - did you mean {hint[0]!r}?" if hint else "")
                      + " (`sdlc config --spec` lists every key)")


def _plain_text(key: str, value: str) -> str:
    if _CONTROL.search(value):
        raise ConfigError(f"{key}: a value may not carry a line break or a control "
                          f"character - neither file format can hold one on a single line")
    return value


def _coerce(q: dict, key: str, raw: object, typed: bool) -> object:
    """What `raw` means as an answer to `q`, or ConfigError naming why it cannot.

    `typed` is an answers FILE, where a list is already a list; otherwise `raw`
    is command-line text, where a list is `a,b` or a JSON array. An empty text
    answer is kept as "" - a clear for a keyed override, a blank for a project
    field - and judged where that difference is known."""
    kind = q["type"]
    if kind in ("list", "multi"):
        if typed:
            items = raw
        else:
            text = str(raw).strip()
            if text.startswith("["):
                try:
                    items = json.loads(text)
                except ValueError as exc:
                    raise ConfigError(f"{key}: {text!r} is not a JSON list ({exc})") from None
            else:
                items = text.split(",")
        if not isinstance(items, list) or not all(isinstance(v, str) for v in items):
            raise ConfigError(f"{key}: a list of strings")
        clean: list[str] = []
        for v in (_plain_text(key, v.strip()) for v in items):
            if v and v not in clean:
                clean.append(v)
        if kind == "multi":
            if bad := [v for v in clean if v not in q["choices"]]:
                hint = get_close_matches(bad[0], q["choices"], n=1)
                raise ConfigError(
                    f"{key}: {', '.join(repr(b) for b in bad)} "
                    f"{'is' if len(bad) == 1 else 'are'} not one of "
                    f"{', '.join(q['choices'])}"
                    + (f" - did you mean {hint[0]!r}?" if hint else ""))
            # In the schema's order - the lifecycle's, for steps - so the same
            # answer always writes the same line, whatever order it was typed in.
            return [c for c in q["choices"] if c in clean]
        return clean
    if kind == "boolean":
        if typed and isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "false"):
            return text == "true"
        raise ConfigError(f"{key}: true or false, not {raw!r}")
    if typed and not isinstance(raw, str):
        raise ConfigError(f"{key}: a string, not {type(raw).__name__}")
    text = _plain_text(key, str(raw).strip())
    if not text:
        if kind == "choice" and not q.get("clearable") and not key.startswith("project."):
            raise ConfigError(f"{key}: one of {', '.join(q['choices'])} - it cannot be blank")
        return ""
    if kind == "choice" and text not in q["choices"]:
        raise ConfigError(f"{key}: {text!r} is not one of {', '.join(q['choices'])}")
    if q.get("pattern") and not re.search(q["pattern"], text):
        raise ConfigError(f"{key}: {text!r} is not provider/model (the pattern is "
                          f"{q['pattern']})")
    return text


def parse_pairs(pairs: list[str], qs: list[dict]) -> dict:
    """`KEY=VALUE` words into one change: {key: (question, gate/step, value)}."""
    change: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ConfigError(f"{pair!r}: write key=value (`sdlc config --spec` lists "
                              f"the keys)")
        _add(change, key, raw, qs, typed=False)
    if not change:
        raise ConfigError("set needs at least one key=value")
    return change


def parse_answers(path: Path, qs: list[dict]) -> dict:
    """An answers file - one JSON object of {key: value} - into one change."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path} could not be read as JSON: {exc}") from None
    if not isinstance(data, dict) or not data:
        raise ConfigError(f"{path} must hold one JSON object of {{key: value}}")
    change: dict = {}
    for key, raw in data.items():
        _add(change, str(key).strip(), raw, qs, typed=True)
    return change


def _add(change: dict, key: str, raw: object, qs: list[dict], typed: bool) -> None:
    if key in change:
        raise ConfigError(f"{key} is given twice in one change")
    q, sub = _question_for(key, qs)
    change[key] = (q, sub, _coerce(q, key, raw, typed))


# ── the files, as lines ───────────────────────────────────────────────────────


class _Doc:
    """A text file as lines, remembering the three things a rewrite must keep:
    its line ending, a byte-order mark, and whether it ends with a newline."""

    def __init__(self, lines: list[str], newline: str = "\n", bom: bool = False,
                 final: bool = True):
        self.lines, self.newline, self.bom, self.final = lines, newline, bom, final

    @classmethod
    def load(cls, path: Path) -> "_Doc":
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ConfigError(f"{path.name} could not be read: {exc}") from None
        bom = raw.startswith(codecs.BOM_UTF8)
        try:
            text = raw[len(codecs.BOM_UTF8) if bom else 0:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{path.name} is not UTF-8 ({exc}) - the wizard edits "
                              f"only what it can read exactly; fix it by hand") from None
        return cls(text.splitlines(), "\r\n" if "\r\n" in text else "\n", bom,
                   text.endswith(("\n", "\r")))

    @classmethod
    def fresh(cls, text: str) -> "_Doc":
        return cls(text.splitlines(), "\n", False, text.endswith("\n"))

    def dump(self) -> bytes:
        body = self.newline.join(self.lines) + (self.newline if self.final else "")
        return (codecs.BOM_UTF8 if self.bom else b"") + body.encode("utf-8")


def _as_read(data: bytes) -> str:
    """These bytes as `policy._profile_source` will read them from disk: decoded
    with its error rule, and newlines translated the way text mode does."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _with_comment(body: str, old: str) -> str:
    """`body` in place of `old`, keeping old's trailing comment in its column."""
    head = guardrails._strip_comment(old)
    comment = old[len(head):]
    if not comment:
        return body
    return body + " " * max(1, len(head) - len(body)) + comment


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def _insert_new(lines: list[str], key: str, new: list[str]) -> list[str]:
    """A declaration the profile does not have yet: placed under the template's
    commented example of it (`# extra_gates: ...`, `# gates:` and its indented
    example rows) where there is one, else at the end of the file."""
    example = re.compile(rf"^#\s*{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if example.match(line):
            j = i + 1
            while j < len(lines) and re.match(r"^#\s{2,}\S", lines[j]):
                j += 1
            return lines[:j] + new + lines[j:]
    tail = [""] if lines and lines[-1].strip() else []
    return lines + tail + new


def _set_top(lines: list[str], key: str, text: str) -> list[str]:
    """`key: text`, where the READERS find `key` - `declared_lines` is their own
    locator, so a block-form list is replaced whole and never half-left."""
    where = sorted(guardrails.declared_lines(lines, key))
    if not where:
        return _insert_new(lines, key, [f"{key}: {text}"])
    first = where[0]
    out = list(lines)
    out[first] = _with_comment(f"{_indent(lines[first])}{key}: {text}", lines[first])
    for i in reversed(where[1:]):
        del out[i]
    return out


def _block(lines: list[str], name: str) -> tuple[int | None, list[tuple[int, str]]]:
    """The top-level `name:` block: its header line, and (line, key) per entry -
    walked with the readers' own row walk, so a block is where they say it is."""
    header, entries = None, []
    stripped = [guardrails._strip_comment(line).rstrip() for line in lines]
    for index, block, line in guardrails._profile_rows(stripped):
        if block != name:
            continue
        if guardrails._names_block(line) == name:
            if header is None:
                header = index
            continue
        entries.append((index, line.strip().partition(":")[0].strip()))
    return header, entries


def _set_entry(lines: list[str], name: str, key: str, text: str) -> list[str]:
    header, entries = _block(lines, name)
    out = list(lines)
    for index, k in entries:
        if k == key:
            out[index] = _with_comment(f"{_indent(lines[index])}{key}: {text}", lines[index])
            return out
    if header is None:
        return _insert_new(out, name, [f"{name}:", f"  {key}: {text}"])
    indent = _indent(lines[entries[0][0]]) if entries else "  "
    at = (entries[-1][0] if entries else header) + 1
    return out[:at] + [f"{indent}{key}: {text}"] + out[at:]


def _clear_entry(lines: list[str], name: str, key: str) -> list[str]:
    """Remove `key` from the block - and the header with it once nothing is
    left, rather than a bare `name:` a reader must interpret."""
    header, entries = _block(lines, name)
    gone = {index for index, k in entries if k == key}
    if not gone:
        return lines
    if header is not None and len(gone) == len(entries):
        gone.add(header)
    return [line for i, line in enumerate(lines) if i not in gone]


def _set_project_block(lines: list[str], rendered: str) -> list[str]:
    """The profile's `project:` block, replaced by the installer's own render of
    it - or put where the installer's not-declared placeholder comment sits."""
    new = rendered.split("\n")
    header, entries = _block(lines, "project")
    if header is not None:
        drop = {header, *(index for index, _ in entries)}
        last = max(drop)
        inside = [lines[i] for i in range(header, last + 1) if i not in drop]
        return lines[:header] + new + inside + lines[last + 1:]
    for i, line in enumerate(lines):
        if line.startswith("# project:"):
            return lines[:i] + new + lines[i + 1:]
    return _insert_new(lines, "project", new)


def _yaml(value: object) -> str:
    """A pipeline value as the profile spells it: bare words (step names, enum
    values, provider/model), a flow list, or true/false. Anything a bare word
    cannot carry is caught by the read-back, not guessed at here."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]"
    return str(value)


#: A question whose declaration sits inside a section, by the name the readers
#: look it up under (`profile_list` finds `block_on` at any depth).
_DECLARED_AS = {"review.block_on": "block_on"}


def _edit_profile(lines: list[str], key: str, sub: str | None, value: object) -> list[str]:
    head = key.split(".", 1)[0]
    if sub is not None or key == "models.default":
        entry = sub if sub is not None else models.DEFAULT_KEY
        if value == "":
            return _clear_entry(lines, head, entry)
        return _set_entry(lines, head, entry, _yaml(value))
    return _set_top(lines, _DECLARED_AS.get(key, key), _yaml(value))


# ── sdlc-project.toml ─────────────────────────────────────────────────────────

_PROJECT_TABLE = re.compile(r"^\s*\[\s*project\s*\]\s*(#.*)?$")
_ANY_TABLE = re.compile(r"^\s*\[")
_TOML_KEY = re.compile(r"^\s*[A-Za-z0-9_-]+\s*=")


def _toml_value(si, value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(si, v) for v in value) + "]"
    return '"' + si._toml_str(str(value)) + '"'


def _toml_extent(lines: list[str], i: int, end: int, field: str) -> tuple[int, str]:
    """(the line after `field`'s value ends, a trailing comment to keep). A value
    may span lines - a long array - so the extent is the first span that parses."""
    for j in range(i + 1, end + 1):
        try:
            tomllib.loads("\n".join(lines[i:j]))
        except tomllib.TOMLDecodeError:
            continue
        if j == i + 1:
            line = lines[i]
            for m in re.finditer(r"\s#", line):
                try:
                    tomllib.loads(line[:m.start()])
                except tomllib.TOMLDecodeError:
                    continue
                return j, line[m.start():]
        return j, ""
    raise ConfigError(f"sdlc-project.toml: `{field}` could not be read as TOML - fix it "
                      f"by hand")


def _toml_set(si, lines: list[str], field: str, value: object) -> list[str]:
    start = next((i for i, line in enumerate(lines) if _PROJECT_TABLE.match(line)), None)
    if start is None:
        raise ConfigError(f"{si.PROJECT_FILE} has no [project] table - copy "
                          f"sdlc_templates/project.example.toml, or delete the file and "
                          f"let sdlc config write the template")
    end = next((i for i in range(start + 1, len(lines)) if _ANY_TABLE.match(lines[i])),
               len(lines))
    rendered = f"{field} = {_toml_value(si, value)}"
    key = re.compile(rf"^\s*{re.escape(field)}\s*=")
    for i in range(start + 1, end):
        if key.match(lines[i]):
            j, comment = _toml_extent(lines, i, end, field)
            return lines[:i] + [rendered + comment] + lines[j:]
    last = max((i for i in range(start + 1, end) if _TOML_KEY.match(lines[i])),
               default=start)
    return lines[:last + 1] + [rendered] + lines[last + 1:]


def _read_project_text(si, data: bytes) -> dict:
    """The candidate file, read by the installer's own reader - the one INIT runs."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / si.PROJECT_FILE
        path.write_bytes(data)
        try:
            return si.read_project(path)
        except si.WorkspaceError as exc:
            raise Refused([str(exc)]) from None


# ── what a run would refuse ──────────────────────────────────────────────────


def run_refusals(target: Path) -> list[str]:
    """Every refusal a run of this configuration would meet at a step's entry or
    at a gate, found now - each check the run's own, in the order a run meets it."""
    try:
        pipe = stage.pipeline(target)
    except _REFUSALS as exc:
        return [str(exc)]             # nothing further resolves without a pipeline
    problems: list[str] = []
    try:
        problems += guardrails.tighten_only(
            guardrails.load(target), guardrails.profile_dials(target),
            guardrails.component_count(target), guardrails.gate_overrides(target),
            phases=stage.declared(target)[0], gates=stage.resolved_hard_gates(target))
    except _REFUSALS as exc:
        problems.append(str(exc))
    for s in pipe.steps:
        try:
            models.model_for(target, s.name)
        except _REFUSALS as exc:
            problems.append(f"{s.name}: {exc}")
    return problems


def _pipeline_lines(target: Path) -> list[str] | None:
    try:
        pipe = stage.pipeline(target)
        return stage.render_text(pipe, set(stage.resolved_hard_gates(target)))
    except _REFUSALS:
        return None


def _read_back(target: Path, profile_keys: dict, project: dict | None) -> list[str]:
    """What the readers a run uses read back from the candidate, against what
    was asked for. A writer is only as good as the reader that consumes it."""
    said: list[str] = []

    def disagree(key: str, want: object, got: object) -> None:
        if got != want:
            said.append(f"{key}: written as {want!r}, but read back through the "
                        f"harness's reader it is {got!r} - the profile cannot carry "
                        f"this value")

    for key, (_q, sub, value) in profile_keys.items():
        want = None if value == "" else value
        try:
            if key in ("phases", "extra_gates", "review.block_on"):
                got = guardrails.profile_list(target, _DECLARED_AS.get(key, key))
                got = None if got is None else [str(v) for v in got]
            elif key in ("gate_profile", "signing"):
                got = guardrails.profile_top_value(target, key)
            elif key == "model_routing":
                got = models.model_routing(target)
            elif key.startswith("gates."):
                got = guardrails.gate_overrides(target).get(sub)
            else:
                got = models.models_declared(target).get(sub or models.DEFAULT_KEY)
        except _REFUSALS as exc:
            said.append(f"{key}: {exc}")
            continue
        disagree(key, want, got)
    if project is not None:
        try:
            block = guardrails.profile_project(target)
        except _REFUSALS as exc:
            return said + [f"project: {exc}"]
        for field, value in project.items():
            disagree(f"project.{field}", value, block.get(field))
    return said


# ── one change ────────────────────────────────────────────────────────────────


def _before(cur: dict, key: str, sub: str | None) -> object:
    if sub is None:
        return cur.get(key)
    qid = "gates.<gate>" if key.startswith("gates.") else "models.<step>"
    return (cur.get(qid) or {}).get(sub)


def _same(before: object, value: object) -> bool:
    return before in (None, "") if value == "" else before == value


def plan(target: Path, change: dict) -> dict:
    """Everything this change would write, validated as a run would validate it
    - or `Refused` with every reason. Writes nothing."""
    si = _installer()
    installed = _installed(target)
    project_keys = {k: v for k, v in change.items() if k.startswith("project.")}
    profile_keys = {k: v for k, v in change.items() if not k.startswith("project.")}
    if profile_keys and not installed:
        keys = ", ".join(profile_keys)
        raise ConfigError(f"{keys} {'lives' if len(profile_keys) == 1 else 'live'} in "
                          f"{PROFILE_REL.as_posix()}, which INIT has not written yet - "
                          f"fill the project fields (sdlc config set project.<field>=...), "
                          f"run INIT, then configure the pipeline")
    toml = target / si.PROJECT_FILE
    block: dict = {}
    if installed and project_keys:
        try:
            block = guardrails.profile_project(target)
        except _REFUSALS as exc:
            raise ConfigError(f"{PROFILE_REL.as_posix()} could not be read: {exc}") from None
        if block and not toml.is_file():
            raise ConfigError(
                f"there is no {si.PROJECT_FILE} here, and the profile's project: block "
                f"came from somewhere else - a workspace's own [project] table, which "
                f"wins at INIT. Edit it there and re-run INIT, or restore "
                f"{si.PROJECT_FILE}; writing a new one here would not be read")
    cur, _unreadable = current_values(target)
    # A project answer is already so only when BOTH homes say so: a profile
    # block left out of step is exactly what setting the value again repairs.
    project_keys = {k: v for k, v in project_keys.items()
                    if not (_same(cur.get(k), v[2])
                            and (not installed or _same(block.get(k.split(".", 1)[1]), v[2])))}
    profile_keys = {k: v for k, v in profile_keys.items()
                    if not _same(_before(cur, k, v[1]), v[2])}
    changed = [{"key": k, "from": _before(cur, k, sub), "to": value}
               for k, (_q, sub, value) in {**project_keys, **profile_keys}.items()]
    result = {"changed": changed, "writes": [], "blank": [], "pipeline": None}
    problems: list[str] = []

    project = None
    if project_keys:
        doc = _Doc.load(toml) if toml.is_file() else _Doc.fresh(si.project_template(target))
        if toml.is_file():
            try:
                si.read_project(toml)
            except si.WorkspaceError as exc:
                raise ConfigError(f"{exc} - fix it by hand first") from None
        for key, (q, _sub, value) in project_keys.items():
            doc.lines = _toml_set(si, doc.lines, key.split(".", 1)[1], value)
        after = doc.dump()
        project = _read_project_text(si, after)
        for key, (q, _sub, value) in project_keys.items():
            field = key.split(".", 1)[1]
            if project[field] != value:
                problems.append(f"{key}: written as {value!r}, but read back from "
                                f"{si.PROJECT_FILE} it is {project[field]!r}")
            if q["required"] and not value:
                problems.append(f"{key} is required - INIT stops on a blank {field}, so "
                                f"give it a value rather than clearing it")
        result["blank"] = si.project_blanks(project)
        result["writes"].append((toml, toml.read_bytes() if toml.is_file() else None, after))

    if installed and (profile_keys or project is not None):
        path = target / PROFILE_REL
        doc = _Doc.load(path)
        for key, (_q, sub, value) in profile_keys.items():
            doc.lines = _edit_profile(doc.lines, key, sub, value)
        if project is not None:
            doc.lines = _set_project_block(doc.lines, si.render_project(project))
        after = doc.dump()
        before_pipeline = _pipeline_lines(target)
        with guardrails.proposed_profile(target, _as_read(after)):
            problems += run_refusals(target)
            problems += _read_back(target, profile_keys, project)
            if not problems:
                pipe = stage.pipeline(target)
                after_pipeline = stage.render_text(pipe, set(stage.resolved_hard_gates(target)))
                diagram = stage.diagram_for(target)
        if not problems:
            result["pipeline"] = {"before": before_pipeline, "after": after_pipeline}
            result["writes"].append((path, path.read_bytes(), after))
            drawn = target / DIAGRAM_REL
            old = drawn.read_bytes() if drawn.is_file() else None
            if old is None or _as_read(old) != diagram:
                result["writes"].append((drawn, old, diagram.encode("utf-8")))
    if problems:
        raise Refused(problems)
    result["writes"] = [w for w in result["writes"] if w[1] != w[2]]
    return result


def _replace(path: Path, data: bytes) -> None:
    """One file, replaced whole: written beside itself, then swapped in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.sdlc-config-{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_all(target: Path, writes: list[tuple[Path, bytes | None, bytes]]) -> list[str]:
    """Every file of one change, or none: a failed write puts back the files
    already replaced, and says so if even that fails."""
    done: list[tuple[Path, bytes | None]] = []
    for path, before, after in writes:
        try:
            _replace(path, after)
        except OSError as exc:
            lost = []
            for back, old in reversed(done):
                try:
                    if old is None:
                        back.unlink()
                    else:
                        _replace(back, old)
                except OSError as again:
                    lost.append(f"{back.name}: {again}")
            raise ConfigError(
                f"could not write {path.name}: {exc} - "
                + ("every file already written was put back" if not lost else
                   "and could not put back " + "; ".join(lost))) from None
        done.append((path, before))
    return [p.relative_to(target).as_posix() for p, _b, _a in writes]


# ── the whole-configuration check ────────────────────────────────────────────


def _diagram_problem(target: Path) -> str:
    try:
        want = stage.diagram_for(target)
    except _REFUSALS:
        return ""                     # the resolver's own refusal is already reported
    try:
        have = (target / DIAGRAM_REL).read_text(encoding="utf-8")
    except FileNotFoundError:
        return (f"{DIAGRAM_REL.as_posix()} is missing - sdlc config redraws it on every "
                f"change; draw it now with `{stage.DIAGRAM_COMMAND}`")
    except OSError as exc:
        return f"{DIAGRAM_REL.as_posix()} could not be read: {exc}"
    if have != want:
        return (f"{DIAGRAM_REL.as_posix()} is stale: it does not match the resolved "
                f"pipeline - redraw it with `{stage.DIAGRAM_COMMAND}` and commit the result")
    return ""


def check(target: Path) -> tuple[list[str], list[str]]:
    """(problems, notes) for the configuration as it stands. Problems are what a
    run or INIT would refuse, or a file out of step with the one it is drawn
    from; notes are what could not be checked."""
    si = _installer()
    problems: list[str] = []
    notes: list[str] = []
    installed = _installed(target)
    toml = target / si.PROJECT_FILE
    project = None
    if toml.is_file():
        try:
            project = si.read_project(toml)
        except si.WorkspaceError as exc:
            problems.append(str(exc))
        else:
            if blank := si.project_blanks(project):
                problems.append(f"{si.PROJECT_FILE}: required and blank: "
                                f"{', '.join(blank)} - INIT stops until they are filled")
    elif not installed:
        problems.append(f"no {si.PROJECT_FILE} here yet - INIT stops until it is filled; "
                        f"sdlc config set project.<field>=... writes it")
    if not installed:
        return problems, notes
    problems += run_refusals(target)
    if project is not None:
        try:
            block = guardrails.profile_project(target)
        except _REFUSALS:
            block = None              # the reader's refusal is already reported
        if block == {}:
            notes.append(f"{PROFILE_REL.as_posix()} declares no project: block (an install "
                         f"from before T442) - sdlc config set project.<field>=... adds it")
        elif block is not None:
            if apart := [f for f in project if block.get(f) != project[f]]:
                problems.append(f"{PROFILE_REL.as_posix()}'s project: block and "
                                f"{si.PROJECT_FILE} disagree about {', '.join(apart)} - "
                                f"runs are stamped from the profile; sdlc config set "
                                f"project.<field>=... writes both")
    if said := _diagram_problem(target):
        problems.append(said)
    try:
        notes += guardrails.profile_limits(target)
    except _REFUSALS:
        pass
    return problems, notes


# ── the CLI ───────────────────────────────────────────────────────────────────


def _show(value: object) -> str:
    if value is None or value == "":
        return "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _report_change(result: dict, wrote: list[str], dry_run: bool, say=print) -> None:
    if not result["changed"]:
        say("nothing to change - every value given is already so")
        return
    say("would apply (dry run - nothing written):" if dry_run else "applied:")
    for row in result["changed"]:
        say(f"  {row['key']}: {_show(row['from'])} -> {_show(row['to'])}")
    pipe = result["pipeline"]
    if pipe:
        if pipe["before"] != pipe["after"]:
            say("pipeline now:")
            for line in pipe["before"] or ["(does not resolve)"]:
                say(f"  {line}")
            say("pipeline after this change:")
        else:
            say("pipeline (this change leaves it as it is):")
        for line in pipe["after"]:
            say(f"  {line}")
    if result["blank"]:
        say(f"still blank, and INIT stops until they are filled: "
            f"{', '.join(result['blank'])}")
    if wrote:
        say(f"wrote {', '.join(wrote)}")


# ── --interactive: the terminal front end (T371b) ────────────────────────────

#: Why `--interactive` will not start, and what to use instead.
NO_TERMINAL = (
    "!! --interactive asks its questions at a terminal, and stdin is not one - so it "
    "refuses rather than wait for answers that cannot come.\n"
    "   From an AI coding tool, run /sdlc-config: it asks the same questions in the chat.\n"
    "   From a script: sdlc config set KEY=VALUE ..., or apply --from answers.json "
    "(--spec lists every key).")


def is_terminal(stream) -> bool:
    """Whether a person can answer at `stream`.

    **`isatty()` alone does not answer that on Windows, measured.** It is True
    for the NUL device - a character device, which answers every read with EOF -
    so spawned with `stdin=DEVNULL` the loop STARTED, asked its first question
    and hit end-of-input where it should have refused by name. On Windows a
    terminal is a CONSOLE, and `GetConsoleMode` succeeds only on a console
    handle."""
    try:
        if not stream.isatty():
            return False
        if os.name != "nt":
            return True
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return False


def _yes(answer: str, default: bool) -> bool:
    text = answer.strip().lower()
    return default if not text else text in ("y", "yes")


def _asked(q: dict, installed: bool, routing: bool) -> bool:
    """The project questions always; the pipeline's once installed; a model
    question only while routing is on - it routes nothing otherwise."""
    if q["id"].startswith("project."):
        return True
    if not installed:
        return False
    return routing if q["id"].startswith("models.") else True


def _ask_one(q: dict, pending: dict, cur: dict, ask, say) -> None:
    key = q["id"]
    shown = pending[key][2] if key in pending else cur.get(key)
    say("")
    say(f"{key} - {q['prompt']}" + ("  (required)" if q.get("required") else ""))
    if q.get("choices"):
        say(f"  choices: {', '.join(q['choices'])}")
    if q.get("locked"):
        say(f"  always run: {', '.join(q['locked'])}")
    for name, preset in (q.get("presets") or {}).items():
        say(f"  {name}: hard gates {', '.join(preset['hard_gates'])}; signing "
            f"{preset['signing']}; blocks on {', '.join(preset['block_on'])}")
    while True:
        raw = ask(f"{key} [{_show(shown)}]: ")
        if not raw.strip():
            return                                   # Enter keeps what is shown
        try:
            pending[key] = (q, None, _coerce(q, key, raw, typed=False))
            return
        except ConfigError as exc:
            say(f"  !! {exc}")


def _ask_keyed(q: dict, qs: list[dict], pending: dict, cur: dict, ask, say) -> None:
    head = q["id"].split(".", 1)[0]
    now = dict(cur.get(q["id"]) or {})
    for key, (_q, sub, value) in pending.items():
        if sub is not None and key.startswith(head + "."):
            if value == "":
                now.pop(sub, None)
            else:
                now[sub] = value
    say("")
    say(f"{q['id']} - {q['prompt']}")
    say(f"  keys: {', '.join(q['keys'])}"
        + (f"; each one of: {', '.join(q['choices'])}" if q.get("choices") else ""))
    example = f"{q['keys'][0]}={q['choices'][0] if q.get('choices') else '<value>'}"
    say(f"  answer like {example}, several separated by commas; {q['keys'][0]}= "
        f"clears one; Enter keeps them all")
    while True:
        shown = ", ".join(f"{k}={v}" for k, v in sorted(now.items())) or "none"
        raw = ask(f"{q['id']} [{shown}]: ")
        if not raw.strip():
            return
        try:
            answers = []
            for pair in raw.split(","):
                name, sep, value = pair.strip().partition("=")
                if not sep:
                    raise ConfigError(f"{pair.strip()!r}: write {example}")
                full = f"{head}.{name.strip()}"
                qq, sub = _question_for(full, qs)
                answers.append((full, qq, sub, _coerce(qq, full, value, typed=False)))
        except ConfigError as exc:
            say(f"  !! {exc}")
            continue
        for full, qq, sub, value in answers:
            pending[full] = (qq, sub, value)
        return


def interactive(target: Path, ask=input, say=print) -> int:
    """The questions at a terminal, then ONE change through `plan` - previewed,
    and written only on a yes. A refusal lists its reasons and offers another
    pass with every answer kept, so a combination a regime needs can be finished
    rather than typed again. Input ending mid-way writes nothing."""
    try:
        qs = questions()
        installed = _installed(target)
        cur, unreadable = current_values(target)
        for line in unreadable:
            say(f"  .. could not read {line}")
        say("Answer each question; Enter keeps the value shown in [brackets]. "
            "Nothing is written until you confirm at the end.")
        if not installed:
            say(f"(the pipeline questions wait for INIT: there is no "
                f"{PROFILE_REL.as_posix()} here yet)")
        pending: dict = {}
        while True:
            for q in qs:
                routing = (pending["model_routing"][2] if "model_routing" in pending
                           else cur.get("model_routing"))
                if not _asked(q, installed, routing is True):
                    continue
                if "keys" in q:
                    _ask_keyed(q, qs, pending, cur, ask, say)
                else:
                    _ask_one(q, pending, cur, ask, say)
            if not pending:
                say("nothing to change")
                return 0
            try:
                result = plan(target, pending)
            except Refused as exc:
                say("!! a run would not accept that, so nothing was written:")
                for problem in exc.problems:
                    say(f"  !! {problem}")
                if _yes(ask("Answer again, keeping what you gave? [Y/n] "), True):
                    continue
                return 1
            if not result["changed"]:
                say("nothing to change - every answer is already so")
                return 0
            say("")
            _report_change(result, [], True, say)
            if not _yes(ask("Write these changes? [y/N] "), False):
                say("Nothing was written.")
                return 0
            say(f"wrote {', '.join(write_all(target, result['writes']))}")
            return 0
    except EOFError:
        say("")
        say("input ended - nothing was written")
        return 2
    except ConfigError as exc:
        say(f"!! {exc}")
        return 2


def _change(target: Path, change: dict, dry_run: bool, as_json: bool) -> int:
    try:
        result = plan(target, change)
    except Refused as exc:
        if as_json:
            print(json.dumps({"applied": False, "dry_run": dry_run, "changed": [],
                              "problems": exc.problems}, indent=2))
        else:
            print("!! refused - a run would not accept the result, so nothing was written:")
            for problem in exc.problems:
                print(f"  !! {problem}")
        return 1
    wrote = [] if dry_run else write_all(target, result["writes"])
    if as_json:
        print(json.dumps({"applied": bool(result["changed"]) and not dry_run,
                          "dry_run": dry_run, "changed": result["changed"],
                          "pipeline": result["pipeline"], "blank": result["blank"],
                          "wrote": wrote, "problems": []}, indent=2))
    else:
        _report_change(result, wrote, dry_run)
    return 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="The configuration wizard's engine: the questions (--spec), one "
                    "change (set / apply), and a check (--check). Never writes a "
                    "configuration a run would refuse.")
    ap.add_argument("--target", metavar="DIR", default=".")
    ap.add_argument("--spec", action="store_true",
                    help="print every question as JSON, with its current value")
    ap.add_argument("--check", action="store_true",
                    help="would a run and INIT accept this configuration? exit 1 if not")
    ap.add_argument("--interactive", action="store_true",
                    help="ask the questions at a terminal, then preview and confirm one "
                         "change; refuses without a terminal")
    ap.add_argument("--from", dest="answers", metavar="FILE",
                    help="with apply: a JSON object of {key: value}")
    ap.add_argument("--dry-run", action="store_true",
                    help="with set / apply: validate and preview, write nothing")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output, for a front end")
    ap.add_argument("action", nargs="?", choices=("set", "apply"),
                    help="set KEY=VALUE ... | apply --from FILE")
    ap.add_argument("pairs", nargs="*", metavar="KEY=VALUE")
    args = ap.parse_intermixed_args(argv)

    chosen = [name for name, on in (("--spec", args.spec), ("--check", args.check),
                                    ("--interactive", args.interactive),
                                    (args.action or "", bool(args.action))) if on]
    if len(chosen) != 1:
        print("!! choose one: --spec, --check, --interactive, set KEY=VALUE ..., or "
              "apply --from FILE")
        return 2
    if args.interactive and args.as_json:
        print("!! --interactive is for a person at a terminal; --json is for a front end "
              "(use --spec and set)")
        return 2
    if args.action == "apply" and (not args.answers or args.pairs):
        print("!! apply takes its answers from --from FILE, and no KEY=VALUE words")
        return 2
    if args.answers and args.action != "apply":
        print("!! --from goes with apply")
        return 2
    if args.dry_run and not args.action:
        print("!! --dry-run goes with set or apply")
        return 2
    target = Path(args.target)
    if why := cli.missing_target(target):
        print(f"!! {why}")
        return 2
    if args.interactive:
        # An agent's tool call has no TTY: `input()` there hangs or reads EOF, so
        # the refusal comes before a single question is asked.
        if not is_terminal(sys.stdin):
            print(NO_TERMINAL)
            return 2
        return interactive(target)
    try:
        if args.spec:
            print(json.dumps(spec(target), indent=2))
            return 0
        if args.check:
            problems, notes = check(target)
            if args.as_json:
                print(json.dumps({"problems": problems, "notes": notes}, indent=2))
            else:
                for note in notes:
                    print(f"  .. [not checked] {note}")
                for problem in problems:
                    print(f"  !! {problem}")
                print(f"REFUSED - {len(problems)} problem(s)." if problems else
                      "OK - a run and INIT would accept this configuration.")
            return 1 if problems else 0
        qs = questions()
        change = (parse_pairs(args.pairs, qs) if args.action == "set"
                  else parse_answers(Path(args.answers), qs))
        return _change(target, change, args.dry_run, args.as_json)
    except ConfigError as exc:
        if args.as_json:
            print(json.dumps({"applied": False, "error": str(exc)}, indent=2))
        else:
            print(f"!! {exc}")
        return 2


def main(argv: list[str] | None = None) -> int:
    return cli.door(_main, argv, "sdlc config", 2)


if __name__ == "__main__":
    raise SystemExit(main())
