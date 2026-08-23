"""P0 intake — item 31: whatever arrives becomes one normalized record, or it
halts with reasons.

A specification reaches this harness as a file someone exported, a ticket body
pasted into a terminal, or a path an integration drops things into
(`SDLC_INTAKE_PATH`). None of those shapes is the pipeline's; all of them are
somebody's. The adapter's whole job is to turn any of them into two artifacts
inside a fresh run folder — `00-intake.md` for the human, `00-intake.json` for the
machine — and to refuse, out loud and with every reason listed, when the thing
it was handed cannot be worked on.

What it checks, and why each one is here rather than later:

* **The spec has a title and a body.** A run is listed by its title and scoped
  from its body; without either there is nothing for P1 to clarify.
* **The working tree is clean.** P5 compares a test run against a baseline
  measured now. Uncommitted edits at intake mean the baseline describes a tree
  nobody can reconstruct, so a dirty tree halts unless the operator overrides
  and the override is recorded in the artifact.
* **The baseline command is captured.** Recorded from `.sdlc/profile.yml`, and
  RUN only when asked (`--baseline`): a suite can take an hour, and a P0 that
  silently spends it is a P0 people learn to skip. When it has not run, the
  record says so in the words P5 needs to read - never a blank that reads as
  "passed".
* **Duplicates are reported, not merged.** The same spec arriving twice is a
  normal accident of ticket exports. Prior runs with the same spec hash or the
  same title are named in the record; the operator decides, because only they
  know whether the second one is a re-run or a real second change.

Scope triage against the graph — which components this change touches — is
deliberately NOT here. That is P2's question, answered by `impact.py` over a
graph with `source_file:line` provenance; guessing it from the prose at intake
would put an unciteable answer in the record where a cited one belongs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import state as ledger  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
#: P0's artifact, on the lifecycle's numbering. It was `intake.md` until item
#: 36 wired the phases onto the spec and found the adapter and the spec
#: naming the same file two things - which meant `stage.py --verify` would
#: forever report P0 as owing something the adapter had already written.
INTAKE_MD, INTAKE_JSON = "00-intake.md", "00-intake.json"
#: The environment variable an integration writes into — a file, or a directory
#: of them. Named in the plan; read here and nowhere else.
INTAKE_ENV = "SDLC_INTAKE_PATH"
#: A body shorter than this is a title with punctuation, not a specification.
MIN_BODY_CHARS = 40

_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9 _/-]{0,40}):[ \t]*(.*)$")
_INLINE_COMMENT = re.compile(r"\s#")


def profile_value(target: Path, key: str) -> str | None:
    """One TOP-LEVEL scalar from `.sdlc/profile.yml`, read with the stdlib.

    Deliberately not YAML: PyYAML is optional in this harness (the extractors
    gate on `props.yaml_available()`), so a hard import here would make intake
    refuse to run in exactly the stripped environment the zero-dependency gate
    protects. A nested key returns None and the caller says so rather than
    inventing a value.

    `Path(target)`, because several modules reach this and some of them hold a
    string. Raising TypeError here made a caller fall back to a default, which
    is how a control goes quiet."""
    path = Path(target) / ".sdlc" / "profile.yml"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith(f"{key}:"):
            continue
        value = line[len(key) + 1:].strip()
        if value[:1] in ("\"", "'"):
            # A quoted scalar ends at its CLOSING quote, and the installer's own
            # writer escapes inner quotes (`_yaml_dq`), so the last unescaped one
            # is the end - cutting an inline comment first would have kept the
            # quotes and handed P5 a command no shell can run.
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
        cut = _INLINE_COMMENT.search(value)
        return (value[:cut.start()] if cut else value).rstrip()
    return None


def resolve_source(spec: str | None, text: str | None, pick: str | None,
                   env: dict[str, str] | None = None) -> tuple[str, str, str]:
    """(body, source_kind, source_name). Explicit beats configured: --text over
    --spec over SDLC_INTAKE_PATH, because the flag is what the operator just
    typed and the variable is what someone set months ago."""
    if text is not None:
        return text, "text", "--text"
    if spec:
        p = Path(spec).expanduser()
        if not p.is_file():
            raise RuntimeError(f"no spec file at {p}")
        return p.read_text(encoding="utf-8", errors="replace"), "file", str(p)
    configured = (env if env is not None else os.environ).get(INTAKE_ENV, "").strip()
    if not configured:
        raise RuntimeError(
            f"nothing to read: pass --spec <file> or --text, or set {INTAKE_ENV} "
            f"to a spec file or a directory of them")
    p = Path(configured).expanduser()
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace"), "env", str(p)
    if not p.is_dir():
        raise RuntimeError(f"{INTAKE_ENV} points at {p}, which does not exist")
    candidates = sorted(f for f in p.iterdir()
                        if f.is_file() and f.suffix.lower() in (".md", ".txt", ".json"))
    if pick:
        chosen = [f for f in candidates if f.name == pick]
        if not chosen:
            raise RuntimeError(f"{pick} is not in {p} "
                               f"({len(candidates)} spec file(s) there)")
        return chosen[0].read_text(encoding="utf-8", errors="replace"), "env", str(chosen[0])
    if len(candidates) == 1:
        return (candidates[0].read_text(encoding="utf-8", errors="replace"),
                "env", str(candidates[0]))
    names = ", ".join(f.name for f in candidates[:8]) or "(none)"
    raise RuntimeError(f"{p} holds {len(candidates)} spec files - name one with "
                       f"--pick: {names}")


def normalize(raw: str) -> dict:
    """Any of the shapes a spec arrives in → title, fields, body.

    The grammar is deliberately small, because the input is somebody else's
    format: a leading `# heading` or a `Title:` field names the run, `Key:
    value` lines in the leading block become fields, and everything after the
    first blank line that follows them is the body, kept VERBATIM. Nothing is
    dropped - an unrecognised line is body, not a parse error."""
    lines = raw.replace("\r\n", "\n").split("\n")
    title, fields, start = "", {}, 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # Blank lines inside the leading block are separators, not its end:
            # the common export puts one between the heading and the fields.
            start = i + 1
            continue
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
            start = i + 1
            continue
        m = _FIELD.match(stripped)
        if m and not stripped.startswith("#"):
            key = m.group(1).strip().lower().replace(" ", "_").replace("-", "_")
            value = m.group(2).strip()
            if key == "title" and not title:
                title = value
            else:
                fields[key] = value
            start = i + 1
            continue
        if not title:
            title = stripped
            start = i + 1
        break
    body = "\n".join(lines[start:]).strip("\n")
    # The raw text travels with the record. Field-lifting is an INDEX, never a
    # removal: a body whose first line reads like `Problem: ...` would other-
    # wise be filed as a field and vanish from the specification the reviewer
    # reads. The intake markdown renders this verbatim, and the hash is over it.
    return {"title": title.strip(), "fields": fields, "body": body,
            "raw": raw.replace("\r\n", "\n").strip("\n")}


def spec_hash(title: str, body: str) -> str:
    """Identity of a specification: its title and body, whitespace-normalized,
    so the same ticket exported twice hashes the same."""
    blob = re.sub(r"\s+", " ", f"{title}\n{body}").strip().lower()
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tree_state(target: Path) -> dict:
    """Whether the tree is clean, or why the question could not be answered."""
    workspace = False
    profile = target / ".sdlc" / "profile.yml"
    if profile.is_file():
        workspace = any(line.startswith("components:")
                        for line in profile.read_text(encoding="utf-8",
                                                      errors="replace").splitlines())
    try:
        proc = subprocess.run(["git", "-C", str(target), "status", "--porcelain"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"clean": None, "dirty_files": None, "workspace": workspace,
                "note": f"git did not run: {exc}"}
    if proc.returncode != 0:
        return {"clean": None, "dirty_files": None, "workspace": workspace,
                "note": (proc.stderr or "git failed").strip().splitlines()[0][:200]}
    dirty = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        # The ledger's own folder is not the change. Intake WRITES a run folder,
        # so counting it would make the first intake dirty the tree for the
        # second one - measured, on the first real run of this tool. Everything
        # else under .sdlc/ (profile, graph) stays visible: it can change what a
        # run does, and a baseline taken over an edited profile is a real risk.
        if path.replace("\\", "/").startswith(".sdlc/runs"):
            continue
        dirty.append(path)
    return {"clean": not dirty, "dirty_files": len(dirty), "workspace": workspace,
            "note": "" if not dirty else "; ".join(dirty[:5])}


def baseline(target: Path, run: bool) -> dict:
    """The suite P5 will compare against. Captured always, executed on request."""
    command = profile_value(target, "test_command")
    row = {"command": command or "", "ran": False, "exit_code": None,
           "seconds": None,
           "note": ("no test_command in .sdlc/profile.yml - P5 has nothing to "
                    "compare against until one is set"
                    if not command else
                    "recorded but NOT run - pass --baseline to execute it")}
    if not command or not run:
        return row
    started = time.time()
    try:
        proc = subprocess.run(command, shell=True, cwd=str(target),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=7200)
        row.update(ran=True, exit_code=proc.returncode,
                   seconds=round(time.time() - started, 1),
                   note=("baseline green" if proc.returncode == 0 else
                         "baseline is RED before any change - P5 compares against "
                         "this, so the failures are pre-existing"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        row.update(ran=False, note=f"baseline did not run: {exc}")
    return row


def duplicates(target: Path, digest: str, title: str) -> list[dict]:
    """Prior runs that look like this one. Reported, never merged."""
    out = []
    for row in ledger.list_runs(target):
        d = ledger.run_dir(target, row["run_id"]) / INTAKE_JSON
        prior = {}
        if d.is_file():
            try:
                prior = json.loads(d.read_text(encoding="utf-8"))
            except ValueError:
                out.append({"run_id": row["run_id"], "why": f"unreadable {INTAKE_JSON}"})
                continue
        if prior.get("spec_sha256") == digest:
            out.append({"run_id": row["run_id"], "why": "same specification"})
        elif title and row.get("title", "").strip().lower() == title.strip().lower():
            out.append({"run_id": row["run_id"], "why": "same title"})
    return out


def validate(norm: dict, kind: str, tree: dict, allow_dirty: bool) -> list[str]:
    """Every reason this cannot proceed, all of them, in one list."""
    problems = []
    if not norm["title"]:
        problems.append("no title - the first '# heading', a 'Title:' field, or the "
                        "first line becomes one")
    if len(norm["raw"]) < MIN_BODY_CHARS:
        problems.append(f"the whole specification is {len(norm['raw'])} characters - "
                        f"P1 can only clarify one of at least {MIN_BODY_CHARS}")
    if kind not in ledger.KINDS:
        problems.append(f"unknown kind {kind!r} - one of {', '.join(ledger.KINDS)}")
    if tree["clean"] is None:
        problems.append(f"could not read the working tree: {tree['note']}")
        if tree.get("workspace"):
            # Measured against a real installed workspace: the estate root holds
            # many repositories and is not itself one, so "is the tree clean"
            # has no single answer there. Saying only "git failed" would send
            # the operator looking for a broken git.
            problems.append("this target is a WORKSPACE root, not a single "
                            "repository - open the run against the component "
                            "repository the change belongs to (a workspace-wide "
                            "run is not this item's job)")
    elif not tree["clean"] and not allow_dirty:
        problems.append(f"{tree['dirty_files']} uncommitted file(s) - P5's baseline "
                        f"would describe a tree nobody can reconstruct "
                        f"(--allow-dirty records the override): {tree['note']}")
    return problems


def render_md(rec: dict, norm: dict) -> str:
    base = rec["baseline"]
    tree = rec["tree"]
    fields = "\n".join(f"| `{k}` | {v} |" for k, v in sorted(norm["fields"].items()))
    dupes = "\n".join(f"- `{d['run_id']}` — {d['why']}" for d in rec["duplicates"])
    return (
        f"# {norm['title']}\n\n"
        f"- **Run**: `{rec['run_id']}`  ·  **Kind**: {rec['kind']}\n"
        f"- **Source**: {rec['source']['name']} ({rec['source']['kind']})\n"
        f"- **Specification hash**: `{rec['spec_sha256']}`\n"
        f"- **Working tree**: "
        + ("clean" if tree["clean"] else f"{tree['dirty_files']} uncommitted file(s)")
        + "\n"
        f"- **Baseline**: "
        + (f"`{base['command']}` — {base['note']}" if base["command"] else base["note"])
        + "\n\n"
        + ("## Fields\n\n| key | value |\n|---|---|\n" + fields + "\n\n" if fields else "")
        + ("## Possible duplicates\n\n" + dupes + "\n\nReported, not merged — only you "
           "know whether this is a re-run or a second change.\n\n" if dupes else "")
        + "## Specification (verbatim)\n\n"
        + (norm["raw"] or "_(empty)_") + "\n\n"
        + "## Not decided here\n\n"
        + "Which components this touches is P2's question, answered from the graph "
          "with `source_file:line` provenance. Intake records what arrived; it does "
          "not guess what it hits.\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="P0 intake: any specification becomes one normalized run record, "
                    "or halts with every reason (item 31).")
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the repository the run belongs to")
    ap.add_argument("--spec", metavar="FILE", help="a specification file")
    ap.add_argument("--text", metavar="STR", help="the specification itself")
    ap.add_argument("--pick", metavar="NAME",
                    help=f"which file, when {INTAKE_ENV} names a directory")
    ap.add_argument("--kind", default="requirement", choices=ledger.KINDS)
    ap.add_argument("--id", default=None, help="run id (default: time + title digest)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="accept an uncommitted tree; the override is recorded")
    ap.add_argument("--baseline", action="store_true",
                    help="RUN the profile's test command now and record the result")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2
    try:
        raw, src_kind, src_name = resolve_source(args.spec, args.text, args.pick)
    except RuntimeError as exc:
        print(f"!! {exc}")
        return 2

    norm = normalize(raw)
    tree = tree_state(target)
    problems = validate(norm, args.kind, tree, args.allow_dirty)
    if problems:
        print(f"!! intake refused - {len(problems)} reason(s), all of them:")
        for p in problems:
            print(f"   - {p}")
        print("   nothing was written; no run was opened.")
        return 2

    digest = spec_hash(norm["title"], norm["raw"])
    dupes = duplicates(target, digest, norm["title"])
    try:
        st = ledger.create(target, args.kind, norm["title"], run_id=args.id)
    except (ValueError, RuntimeError) as exc:
        print(f"!! {exc}")
        return 2

    rec = {
        "schema_version": SCHEMA_VERSION, "kind": args.kind,
        "run_id": st["run_id"], "at": st["created_at"],
        "source": {"kind": src_kind, "name": src_name},
        "title": norm["title"], "fields": norm["fields"],
        "spec_sha256": digest, "body_chars": len(norm["body"]), "spec_chars": len(norm["raw"]),
        "tree": tree, "tree_override": bool(args.allow_dirty and not tree["clean"]),
        "baseline": baseline(target, args.baseline),
        "duplicates": dupes,
    }
    d = ledger.run_dir(target, st["run_id"])
    (d / INTAKE_JSON).write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8", newline="\n")
    (d / INTAKE_MD).write_text(render_md(rec, norm), encoding="utf-8", newline="\n")
    for name in (INTAKE_MD, INTAKE_JSON):
        ledger.record_artifact(target, st["run_id"], name)
    ledger.advance(target, st["run_id"], phase="P0", status="running",
                   note="intake normalized and recorded")

    print(f"run {st['run_id']}: {norm['title']}")
    print(f"  intake  : {d / INTAKE_MD}")
    print(f"  baseline: {rec['baseline']['command'] or '(none)'} "
          f"- {rec['baseline']['note']}")
    if rec["tree_override"]:
        print(f"  NOTE: accepted {tree['dirty_files']} uncommitted file(s) "
              f"(--allow-dirty); the baseline describes that tree, not HEAD.")
    for dup in dupes:
        print(f"  NOTE: possible duplicate of {dup['run_id']} ({dup['why']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
