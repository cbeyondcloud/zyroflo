"""Per-component graph shards: the committed snapshot, and the checks that keep it honest.

Each component commits into its own `<component>/.sdlc/graph/` exactly the facts extracted
from ITS OWN files. The union of the shards is the graph; anything derived from the whole
graph — canonical damping, resolved hosts — is recomputed at load and never committed,
because a derived value parked in whichever shard is convenient is a value whose provenance
nobody can check.

Three rules, each load-bearing:

* **Facts only, and no run metadata.** A shard is byte-identical across rebuilds of the
  same tree or it is useless as a committed artifact — every rebuild would diff, the diff
  would be noise, and reviewers learn to ignore it (the exact failure Rev 5 recorded when
  `run_id` reached the serialized record).
* **The digest is part of the snapshot.** `snapshot.json` records a sha256 over the shard
  bytes, so a hand-edited or half-written shard is detectable without rebuilding — and a
  loader that cannot tell a stale shard from a fresh one repeats item 80's lesson at
  estate scale. It records the parser state the shard was built under too (`parsers`,
  batch 16): each parser the readers of the component's files needed, and its version - or
  `false` where it was missing (batch 22, T631). The shard bytes are byte-identical either
  way; only the manifest moves.
* **A partial set loads loudly.** A developer who cloned 3 of 26 components has 3 shards.
  The graph they get answers for what is present and SAYS what is absent; silently
  answering impact queries from an incomplete set is the "empty reads exactly like clean"
  failure this project keeps meeting.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.graph.core.lib import emit
from tools.graph.core.lib.repo_walk import os_error_word
from tools.graph.core.model import ExtractResult

#: Where a component's shard lives, relative to the component root. The same directory the
#: chained single-repo build writes its resolved view into: the shard is the committed
#: HALF of that directory, and item 80's generated `.sdlc/.gitignore` deliberately ignores
#: neither.
SHARD_DIR = Path(".sdlc") / "graph"
SHARD = "shard.jsonl"
SHARD_UNRESOLVED = "shard.unresolved.jsonl"
MANIFEST = "snapshot.json"

#: The shard directory's own `.gitattributes` (batch-2 review, F3 - T483's class). A shard
#: is checked by the digest of its raw bytes, so git's line-ending conversion
#: (`core.autocrlf=true`, the Windows default) made a fresh clone's shards read as
#: MISMATCHED and `--from-snapshot` refuse them. Written HERE rather than by the installer
#: because every component commits its own shard in its OWN repository, which the
#: installer never touches. It names only the three files a digest reads: the build's
#: other outputs share this directory, and `-text` on them would freeze whichever
#: platform's newline they were written with into the commit.
GITATTRIBUTES = ".gitattributes"
SHARD_ATTRIBUTES = f"""\
# Written by the graph snapshot (tools/graph/snapshot.py).
# These are checked by the digest of their exact bytes, so git must not convert
# their line endings on checkout or commit (core.autocrlf).
{SHARD} -text
{SHARD_UNRESOLVED} -text
{MANIFEST} -text
"""


def shard_dir(root: Path, component: dict) -> Path:
    return root / component["path"] / SHARD_DIR


@dataclass(frozen=True)
class ManifestRead:
    """One reading of a shard's manifest: `absent` only when nothing exists at its path,
    `damaged` with the cause, or `ok` with the manifest (batch 16)."""

    state: str
    cause: str | None = None
    manifest: dict | None = None
    remedy: str | None = None


#: The stamp block's types (batch 16, design rounds 2 and 5): a key that is present must
#: have its type; a missing key is "no stamps" or "no parser stamp", never a damage - but a
#: manifest with no `sha256` cannot be verified at all. A parser's value is its version, a
#: non-empty string, or `false` where it was missing; a legacy `true` (batch 16's stamp)
#: still loads, read as "no version recorded" and stale (batch 22, D22-1).
def _stamp_block_fault(recorded: dict) -> str | None:
    if "sha256" not in recorded:
        return "it records no digest"
    for key in ("sha256", "extractor_version", "config_sha"):
        if key in recorded and not isinstance(recorded[key], str):
            return f"`{key}` is not a string"
    inputs = recorded.get("inputs", {})
    if not isinstance(inputs, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in inputs.items()):
        return "`inputs` is not a mapping of strings"
    parsers = recorded.get("parsers", {})
    if not isinstance(parsers, dict) or not all(
            isinstance(k, str) and (type(v) is bool or (isinstance(v, str) and v))
            for k, v in parsers.items()):
        return "`parsers` is not a mapping of versions"
    return None


def read_manifest(dir_: Path) -> ManifestRead:
    """One shard's manifest, and why it cannot be trusted when it cannot (batch 16).

    Absent and damaged are told apart: absent is a component with no shard; damaged is a
    shard whose recorded state cannot be read - an `OSError`, a path that is not a regular
    file, bytes that are not UTF-8, a document that is not JSON (nesting past the decoder
    included) or not an object, or a stamp block with the wrong types - and every door that
    reads a manifest (`--check-fresh`, `--from-snapshot`, `--incremental`, `--check-snapshot`)
    shares this one reading. It used to fold all of them into None, so `--check-fresh` called
    a damaged manifest "no committed shard" while the load called it a digest mismatch."""
    manifest = dir_ / MANIFEST
    if not os.path.lexists(manifest):
        return ManifestRead("absent")
    if not manifest.is_file():
        return ManifestRead("damaged", "not a regular file", remedy="remove it, then re-publish")
    try:
        raw = manifest.read_bytes()
    except OSError as exc:
        # The cause in words no locale changes (T638): a load's notes carry it, and a
        # repository commits them.
        return ManifestRead("damaged", os_error_word(exc),
                            remedy="fix what blocks reading it, then re-publish")
    try:
        recorded = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return ManifestRead("damaged", "not UTF-8", remedy="re-publish")
    except (ValueError, RecursionError):
        return ManifestRead("damaged", "not JSON", remedy="re-publish")
    if not isinstance(recorded, dict):
        return ManifestRead("damaged", "not a JSON object", remedy="re-publish")
    if fault := _stamp_block_fault(recorded):
        return ManifestRead("damaged", fault, remedy="re-publish")
    return ManifestRead("ok", manifest=recorded)


def _digest(dir_: Path) -> str:
    """sha256 over the shard's two files. Missing files hash as empty, so a shard that
    lost its unresolved half reads as changed, not as clean."""
    h = hashlib.sha256()
    for name in (SHARD, SHARD_UNRESOLVED):
        p = dir_ / name
        h.update(p.read_bytes() if p.exists() else b"")
        h.update(b"\x00")
    return h.hexdigest()


def write_shard(dir_: Path, facts: ExtractResult, stamps: dict | None = None) -> str:
    """Write one component's facts and stamp the manifest. Returns the digest.

    `stamps` is the item-12 freshness layer: per-extractor input digests plus the
    extractor version and config digest the shard was built under, and (batch 16) the
    `parsers` the readers of its files needed, each with its version or `false` where it
    was missing (batch 22) - which leaves the shard's own bytes as they were. Every value is
    **content-derived** — the same tree, workspace config, harness version and installed
    parser versions produce the same bytes — so item 13's no-run-metadata rule holds: a
    rebuild of an unchanged tree still diffs nothing. A commit SHA would not survive that
    test (HEAD moves without the component's files moving), which is why currency is
    answered by content and the commit stays in the build report as provenance for humans.
    """
    emit.write_jsonl(dir_ / SHARD, facts, extractor="shard")
    emit.write_unresolved(dir_ / SHARD_UNRESOLVED, facts.unresolved)
    digest = _digest(dir_)
    # 3 since batch 22 (D22-24): a parser's version is a new shape. The number gates no
    # reading: the reader keys on the keys a manifest holds (D22-34).
    manifest = {"schema_version": 3, "sha256": digest, **(stamps or {})}
    # LF on every platform (batch-2 review, F3): committed beside the shards it digests.
    (dir_ / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        newline="\n")
    # Only when absent: identical bytes on every publish, and never over a line
    # someone added to it on purpose.
    attributes = dir_ / GITATTRIBUTES
    if not attributes.exists():
        attributes.write_text(SHARD_ATTRIBUTES, encoding="utf-8", newline="\n")
    return digest


class PublishRefused(Exception):
    """A publish that cannot write its shards (batch 16; D16-22, D16-49): the pre-flight's
    "cannot write `<path>`: <cause>", raised before anything is written, or a write failing
    part-way, which names in its text the components it had already written."""


def _unwritable(root: Path, dir_: Path) -> str | None:
    """Why a publish could not write this shard directory, or None (D16-22): the directory
    makeable, and each of its four files absent or a regular file. A directory at
    `.gitattributes` is refused deliberately (D16-41): its `-text` lines could never be
    written, a line-ending digest failure waiting for the next checkout."""
    for path in (dir_, *dir_.parents):
        if os.path.lexists(path):
            if not os.path.isdir(path):
                return f"cannot write `{_shown(root, path)}`: not a directory"
            break
    for name in (SHARD, SHARD_UNRESOLVED, MANIFEST, GITATTRIBUTES):
        path = dir_ / name
        if os.path.lexists(path) and not os.path.isfile(path):
            return f"cannot write `{_shown(root, path)}`: not a regular file"
    return None


def publish(root: Path, components: list[dict],
            facts_by_component: dict[str, ExtractResult],
            stamps_by_component: dict[str, dict] | None = None) -> dict[str, str]:
    """Write every component's shard. Returns {component: digest}.

    Every path is checked before anything is written, so a publish that cannot write
    refuses (`PublishRefused`) where it crashed part-way; a write that still fails - a full
    disk, a permission - refuses too, naming the components it had already written."""
    stamps = stamps_by_component or {}
    for comp in components:
        if why := _unwritable(root, shard_dir(root, comp)):
            raise PublishRefused(why)
    written: dict[str, str] = {}
    for comp in components:
        dir_ = shard_dir(root, comp)
        try:
            written[comp["name"]] = write_shard(dir_, facts_by_component[comp["name"]],
                                                stamps.get(comp["name"]))
        except OSError as exc:
            done = f"; already written: {', '.join(written)}" if written else ""
            raise PublishRefused(f"cannot write `{_shown(root, dir_)}`: "
                                 f"{os_error_word(exc)}{done}") from exc
    return written


def stamps_of(recorded: dict | None) -> dict | None:
    """The freshness stamps a manifest carries - the reading hands it the manifest it already
    holds, so it is never read twice (design round 5, D16-50) - or None.

    None is a manifest with no `inputs`: published before stamps existed, so there is nothing
    to compare against. `parsers` (batch 16) rides along only where the manifest has it: its
    absence is claim 7's "published before parser stamps existed", never an empty stamp."""
    if recorded is None or "inputs" not in recorded:
        return None
    stamps = {"inputs": recorded["inputs"],
              "extractor_version": recorded.get("extractor_version", ""),
              "config_sha": recorded.get("config_sha", ""),
              "sha256": recorded.get("sha256", "")}
    if "parsers" in recorded:
        stamps["parsers"] = recorded["parsers"]
    return stamps


@dataclass
class ShardReading:
    """One reading of a component's committed shard (batch 16). `absent` is "no shard" or
    "no stamps"; `refused` the first reason it cannot be trusted, with its `remedy` and kind,
    and the load's own words where they differ (`refused_load`, the digest class); `facts`
    holds its rows always, and its nodes and edges when they were kept."""

    absent: str | None = None
    refused: str | None = None
    refused_load: str | None = None
    remedy: str | None = None
    refusal_kind: str | None = None
    stamps: dict | None = None
    facts: ExtractResult | None = None


class _Unreadable(Exception):
    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


def _rows(path: Path) -> list[dict]:
    """A shard's unresolved rows; an absent file is no rows, as `_digest` reads it."""
    if not path.exists():
        return []
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise _Unreadable("not UTF-8") from None
    rows = []
    # On the newline `write_unresolved` joins with, never `splitlines()`, which also ends a
    # line at the U+2028, U+2029 and U+0085 a row holds raw (T634).
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            raise _Unreadable(f"line {number}: not JSON") from None
        if not isinstance(row, dict):
            raise _Unreadable(f"line {number}: not a JSON object")
        rows.append(row)
    return rows


def _shown(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def _bytes_fault(root: Path, dir_: Path) -> tuple[str, str] | None:
    """The first shard file that cannot be read, with its words and remedy, or None."""
    for name in (SHARD, SHARD_UNRESOLVED):
        path = dir_ / name
        if not os.path.lexists(path):
            continue
        shown = _shown(root, path)
        if not path.is_file():
            return (f"its shard file `{shown}` could not be read (not a regular file)",
                    "remove it, then re-publish")
        try:
            with path.open("rb"):
                pass
        except OSError as exc:
            return (f"its shard file `{shown}` could not be read ({os_error_word(exc)})",
                    "fix what blocks reading it, then re-publish")
    return None


#: The digest class's two renderings, kept word for word (ruling 29).
DIGEST_WORDS = "its shard bytes no longer match their recorded digest"
DIGEST_LOAD_WORDS = ("its shard does not match its own recorded digest; a hand edit or a "
                     "half-written publish. Rebuild and re-publish it")


def read_shard(root: Path, component: dict, *, keep_facts: bool = True) -> ShardReading:
    """The one reading of a component's committed shard (batch 16, T628): it never raises for
    a damaged shard, and every path that judges a shard - `--check-fresh`, the load,
    `--incremental` - reads it here, once.

    The refusals, first found first (each with a remedy that is accepted when followed):
    the manifest cannot be read; a shard file cannot be read, or the facts file is missing
    beside a manifest (`write_jsonl` always writes a header, so it is never legitimately
    absent - an absent rows file reads as an empty one); the bytes do not match the recorded
    digest; the facts cannot be read (`ShardSchemaError`, `ShardRowError`, not UTF-8); the
    rows cannot be read. The catches name exactly those, and `ValueError`/`RecursionError`
    only around a decode - a bug anywhere else still reaches a person as itself."""
    dir_ = shard_dir(root, component)
    manifest = read_manifest(dir_)
    if manifest.state == "absent":
        return ShardReading(absent="no shard")
    if manifest.state == "damaged":
        return ShardReading(refused=f"its committed {MANIFEST} exists but could not be read "
                                    f"({manifest.cause})",
                            remedy=manifest.remedy, refusal_kind="manifest")
    recorded = manifest.manifest
    stamps = stamps_of(recorded)
    reading = ShardReading(absent=None if stamps else "no stamps", stamps=stamps)
    if fault := _bytes_fault(root, dir_):
        reading.refused, reading.remedy = fault
        reading.refusal_kind = "bytes"
        return reading
    if not (dir_ / SHARD).exists():
        reading.refused = f"its shard file `{_shown(root, dir_ / SHARD)}` is missing"
        reading.remedy, reading.refusal_kind = "re-publish", "missing-facts"
        return reading
    try:
        digest = _digest(dir_)
    except OSError as exc:
        reading.refused = (f"its shard file in `{_shown(root, dir_)}` could not be read "
                           f"({os_error_word(exc)})")
        reading.remedy, reading.refusal_kind = "fix what blocks reading it, then re-publish", "bytes"
        return reading
    if digest != recorded["sha256"]:
        reading.refused, reading.refused_load = DIGEST_WORDS, DIGEST_LOAD_WORDS
        reading.remedy, reading.refusal_kind = "re-publish", "digest"
        return reading
    try:
        facts = emit.read_jsonl(dir_ / SHARD)
    except UnicodeDecodeError:
        cause = "not UTF-8"
    except (emit.ShardSchemaError, emit.ShardRowError) as exc:
        # The reader names the file by the path it was handed; the reason names it by its
        # place in the workspace, as every other refusal does, since the load's notes and
        # `--incremental`'s reasons ride into a build report adopters commit (item 57).
        cause = str(exc).replace(str(dir_ / SHARD), _shown(root, dir_ / SHARD))
    else:
        cause = None
    if cause is not None:
        reading.refused = f"its shard's facts could not be read ({cause})"
        reading.remedy, reading.refusal_kind = "re-publish", "facts"
        return reading
    try:
        rows = _rows(dir_ / SHARD_UNRESOLVED)
    except _Unreadable as exc:
        reading.refused = f"its shard's rows could not be read ({exc.cause})"
        reading.remedy, reading.refusal_kind = "re-publish", "rows"
        return reading
    # The rows the facts reader counted - a record of a kind it does not know (zero silent
    # drops) - ride with the rows file's, named by the component and the shard's place in
    # it, never by the path the reader was handed (review B16-2; item 57).
    counted = [{**row, "component": component["name"],
                "source_file": (SHARD_DIR / SHARD).as_posix()} for row in facts.unresolved]
    reading.facts = facts if keep_facts else ExtractResult()
    reading.facts.unresolved = [*counted, *rows]
    return reading


def unreadable_cause(root: Path, component: dict) -> str | None:
    """`--check-snapshot`'s words for a shard it could not look at, where they are not its
    manifest's (design round 4, D16-40): a shard file that cannot be read."""
    dir_ = shard_dir(root, component)
    if read_manifest(dir_).state != "ok":
        return None
    fault = _bytes_fault(root, dir_)
    return fault[0] if fault else None


def load(root: Path, components: list[dict]
         ) -> tuple[ExtractResult, list[str], list[str], list[str]]:
    """Union the committed shards. Returns (facts, present, missing, mismatched).

    The library wrapper over `read_shard` (batch 16), kept with its four-tuple for its pins:
    `missing` is a component with no shard; `mismatched` every shard the reading refused -
    its digest, its manifest, its bytes, its facts or its rows - whose rows are NOT loaded,
    since a shard this build cannot trust is evidence about nothing. The reading catches
    exactly the refusals it names, so a bug in a reader still reaches a person as the
    traceback a bug should be (round 4 measured a bare `ValueError` catch turning one bug
    into "every good shard is corrupt, rebuild and re-publish")."""
    merged = ExtractResult()
    present: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    for comp in components:
        reading = read_shard(root, comp)
        if reading.absent == "no shard":
            missing.append(comp["name"])
        elif reading.refused:
            mismatched.append(comp["name"])
        else:
            merged.extend(reading.facts)
            present.append(comp["name"])
    return merged, present, missing, mismatched


def check(root: Path, components: list[dict],
          facts_by_component: dict[str, ExtractResult]
          ) -> tuple[list[str], list[str], list[str], list[str]]:
    """Rebuild == snapshot, per shard. Returns (drifted, unpublished, unreadable,
    uncomparable).

    Serializes the fresh facts through the same emitter the shards were written with and
    byte-compares — a second serializer here would be a second chance to serialize
    differently, which is the drift this check exists to catch. Per-shard on purpose: a
    drifting extractor localises to the component whose files it read.

    **`uncomparable` is COULD-NOT-LOOK on the FRESH side, and it is none of the other
    three (T425).** The byte-compare needs the fresh facts turned INTO shard bytes first —
    `write_shard` runs the same emitter, whose `Node.to_jsonl`/`Edge.to_jsonl` call
    `json.dumps`. An extractor that puts a non-JSON value in a node's or edge's `props` (a
    `set`, `bytes`, a circular structure) is valid in memory but cannot be serialized, so
    `json.dumps` raises. That raise used to propagate uncaught out of this library, up
    through `build.py`'s `--check-snapshot` path, and reach the person running the door as a
    traceback with exit 1 — a question answered with NO answer, where a clean tree exits 0
    and a real drift exits 2. Refusing to fall back to a raw count of the pool (a confident
    wrong answer) and refusing to crash (no answer), the honest verdict is could-not-look:
    this build cannot produce comparable bytes for this component, so it cannot decide
    whether the committed shard matches, and it says so — distinct from "matches",
    "drifted" and the manifest-side `unreadable`. The catch is `(TypeError, ValueError)`
    around ONLY the fresh serialization, the two types `json.dumps` raises when it cannot
    serialize (unserializable type; circular reference); it is safe here in a way the
    `load` sibling's reader catch is NOT, because these facts are already-constructed model
    objects (their `__post_init__` refusals ran at extraction, not here), so nothing in
    this write path raises those types except the serializer. The loop continues past a
    poisoned component so every uncomparable one is NAMED (zero silent drops), not just the
    first.

    **`unreadable` is COULD-NOT-LOOK, and it is neither of the other two (T436).** The
    drift comparison is over the shard's own two files; the manifest is where the shard's
    recorded digest and freshness stamps live, and it used to be consulted here only for
    `MANIFEST.exists()` — a shard whose manifest existed but was corrupt (invalid JSON, a
    conflict marker, the wrong shape) recomputed a matching digest from its intact bytes
    and reported "matches", exit 0. That is this project's own rule inverted: an artifact
    that cannot be read is not evidence the tree is clean. But calling the loader on it and
    reporting DRIFT would be the same wrong answer in the other direction — a corrupt
    manifest is not evidence the bytes changed either. So a manifest that is present but
    `read_manifest` cannot parse is folded into `unreadable`: this check cannot verify the
    shard, and says so, distinct from both "matches" and "drifted". The caller turns it
    into a printed could-not-look refusal and a non-zero exit, the same shape it already
    gives `drifted` — not a silent success and not a false drift.

    **It owns its scratch and takes none, which is item 133.** `build.py` used to
    hand it `out / "snapshot-check"`, and `out` IS the committed `.sdlc/graph/` —
    so asking whether the snapshot still matched WROTE INTO THE REPOSITORY IT
    AUDITS. Measured on a real install: 3 files, 837 KB, `git check-ignore` exits
    1 so it is not ignored, and `intake.dirty_paths` counts it — meaning the next
    `intake.open_run` would refuse the tree by name over a file the harness wrote.
    The FIFTH instance of *a check that writes* after items 96, 100, 65 and 120,
    and item 120's F6 one flag along in the same module: that item fixed
    `--check-fresh` and left its neighbour standing, which is this project's
    dominant shape.

    Two things were measured before choosing the fix. Nothing reads this scratch
    after `_digest` — it is compared and never returned, so no durable location
    is owed — and `publish` takes no scratch at all, so the path is shared with
    nothing. So the PARAMETER goes rather than being redirected: a location
    nobody outside can need is one nobody outside should be able to choose, and
    that makes the defect unrepresentable rather than merely fixed. `tempfile`
    rather than the harness's own scratch directory, because that one lives
    inside the target repository too and this check should write nowhere near
    any repository, ever.
    """
    drifted: list[str] = []
    unpublished: list[str] = []
    unreadable: list[str] = []
    uncomparable: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sdlc-snapshot-check-") as tmp:
        scratch = Path(tmp)
        for comp in components:
            committed = shard_dir(root, comp)
            if not (committed / MANIFEST).exists():
                unpublished.append(comp["name"])
                continue
            # A manifest that is PRESENT but cannot be parsed is could-not-look, not a
            # clean tree: the `MANIFEST.exists()` line above answered "is there a shard to
            # check?", and a corrupt manifest is a shard whose recorded state this check
            # can no longer read. Byte-comparing the shard files regardless would recompute
            # a matching digest from intact bytes and report "matches" (T436) — a confident
            # wrong answer — so the shard is refused as unverifiable instead. `read_manifest`
            # folds invalid-JSON, wrong-shape and not-a-file into None the same way its
            # `load` sibling does; `.exists()` already separated absent from present.
            if read_manifest(committed).state != "ok" or _bytes_fault(root, committed):
                unreadable.append(comp["name"])
                continue
            fresh = scratch / comp["name"]
            try:
                # The fresh facts must become shard bytes before anything can be compared;
                # a node/edge whose `props` hold a non-JSON value serializes in neither the
                # publish nor this check. This raise used to escape as an uncaught traceback
                # (exit 1) instead of the controlled non-zero verdict the door already knows
                # how to give (T425). NOT folded into the pool with a raw count (a confident
                # wrong answer) and NOT reported as DRIFT (the committed bytes are not
                # evidence here) — could-not-look, on the same exit ladder as `unreadable`.
                write_shard(fresh, facts_by_component[comp["name"]])
            except (TypeError, ValueError):
                uncomparable.append(comp["name"])
                continue
            if _digest(fresh) != _digest(committed):
                drifted.append(comp["name"])
    return drifted, unpublished, unreadable, uncomparable
