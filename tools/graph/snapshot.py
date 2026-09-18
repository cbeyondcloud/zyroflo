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
  estate scale.
* **A partial set loads loudly.** A developer who cloned 3 of 26 components has 3 shards.
  The graph they get answers for what is present and SAYS what is absent; silently
  answering impact queries from an incomplete set is the "empty reads exactly like clean"
  failure this project keeps meeting.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from tools.graph.core.lib import emit
from tools.graph.core.model import ExtractResult

#: Where a component's shard lives, relative to the component root. The same directory the
#: chained single-repo build writes its resolved view into: the shard is the committed
#: HALF of that directory, and item 80's generated `.sdlc/.gitignore` deliberately ignores
#: neither.
SHARD_DIR = Path(".sdlc") / "graph"
SHARD = "shard.jsonl"
SHARD_UNRESOLVED = "shard.unresolved.jsonl"
MANIFEST = "snapshot.json"


def shard_dir(root: Path, component: dict) -> Path:
    return root / component["path"] / SHARD_DIR


def read_manifest(dir_: Path) -> dict | None:
    """One shard's manifest, or None if there isn't one that can be read.

    Absent and damaged are folded here ON PURPOSE, and it is the one place in
    this pass where that is right: both callers ask the same question — *can I
    trust this shard?* — and both already have an answer for no. Absent is not
    silently dropped either way, because the shard it belongs to is named in
    `missing` or `mismatched` and reported to the operator.
    """
    manifest = dir_ / MANIFEST
    if not manifest.is_file():
        return None
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8",
                                                 errors="replace"))
    except ValueError:
        return None
    return recorded if isinstance(recorded, dict) else None


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
    extractor version and config digest the shard was built under. Every value is
    **content-derived** — the same tree, workspace config and harness version produce the
    same bytes — so item 13's no-run-metadata rule holds: a rebuild of an unchanged tree
    still diffs nothing. A commit SHA would not survive that test (HEAD moves without the
    component's files moving), which is why currency is answered by content and the commit
    stays in the build report as provenance for humans.
    """
    emit.write_jsonl(dir_ / SHARD, facts, extractor="shard")
    emit.write_unresolved(dir_ / SHARD_UNRESOLVED, facts.unresolved)
    digest = _digest(dir_)
    manifest = {"schema_version": 2, "sha256": digest, **(stamps or {})}
    (dir_ / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return digest


def publish(root: Path, components: list[dict],
            facts_by_component: dict[str, ExtractResult],
            stamps_by_component: dict[str, dict] | None = None) -> dict[str, str]:
    """Write every component's shard. Returns {component: digest}."""
    stamps = stamps_by_component or {}
    return {comp["name"]: write_shard(shard_dir(root, comp),
                                      facts_by_component[comp["name"]],
                                      stamps.get(comp["name"]))
            for comp in components}


def stamps_of(root: Path, component: dict) -> dict | None:
    """The freshness stamps a committed shard carries, or None.

    None covers both "no shard" and "published before stamps existed" (schema v1) — in
    either case there is nothing to compare against, so the component reads as stale for
    freshness and as not-reusable for an incremental build, which is the safe answer.
    """
    recorded = read_manifest(shard_dir(root, component))
    if recorded is None or "inputs" not in recorded:
        return None
    return {"inputs": recorded["inputs"],
            "extractor_version": recorded.get("extractor_version", ""),
            "config_sha": recorded.get("config_sha", ""),
            "sha256": recorded.get("sha256", "")}


def load(root: Path, components: list[dict]
         ) -> tuple[ExtractResult, list[str], list[str], list[str]]:
    """Union the committed shards. Returns (facts, present, missing, mismatched).

    `mismatched` names components whose shard bytes no longer match their recorded
    digest — a hand edit, a half-written publish, a merge gone wrong. Their rows are NOT
    loaded: a shard that fails its own manifest is evidence about nothing.

    A shard whose BYTES verify but whose header declares a `schema_version` this build
    does not understand, carries no header or a duplicate one (`emit.ShardSchemaError`),
    or whose row content the model refuses — `rows.RowFieldError` for a field its kind
    promised (round 3: an edge missing `provenance` crashed this same door one refusal
    over), an extra key the model does not accept, or the model's own `__post_init__`
    checks (`emit.ShardRowError`, round 4) — is folded into `mismatched` too, for the same
    reason: stale evidence this reader cannot trust is not better than absent evidence,
    whichever of the two named ways made it untrustworthy. That is a CLI's contract, not a
    library's — `build_from_snapshot` already turns `mismatched` into a printed REFUSING
    line and a non-zero exit for every OTHER reason a shard is refused, and a raised
    exception here would bypass that door and reach a person as an unhandled traceback
    instead of the refusal this build already knows how to print.

    CORRECTED AGAIN (round 4): the catch used to be a bare `except ValueError`, which is
    NOT the same claim as "these two named things". A reviewer injected one line of
    nonsense into `emit.read_jsonl` — a conversion with no valid input — and every one of
    seven good, untampered shards was reported "does not match its own recorded digest"
    with an instruction to rebuild and re-publish, which would have overwritten them: a
    single bug in this reader told an operator their whole estate was corrupt and to
    destroy the evidence. `ValueError` and `TypeError` are used BOTH for the model's
    deliberate, bare refusals (see `emit.ShardRowError`'s own docstring) AND for whatever
    an unrelated bug in this codebase happens to raise, and nothing about the TYPE alone
    tells the two apart — so the catch now names exactly `emit.ShardSchemaError` and
    `emit.ShardRowError`, the two things this module raises ON PURPOSE, and nothing wider.

    CORRECTED (round 3): the printed wording still names "its own recorded digest" for
    every name in the list, which is not the literal reason for a schema mismatch, a
    missing header, or a malformed row — a real defect, since a person can recompute the
    digest, find it matches, and conclude the tool itself is broken; it is filed as its
    own row rather than fixed here. The claim this docstring used to make about WHY —
    that "item 19's core gate keeps this fix from editing" `build.py` — was checked by a
    reviewer and is false: that gate diffs `tools/graph/core/` only, and neither
    `build.py` nor this file is under it, so nothing about the gate stops either edit.
    The real constraint is that `build.py` was held by another agent for the duration of
    this row, which is a coordination fact belonging in the row, not a property of the
    gate — recorded here only so the reason is not mis-stated a second time.
    """
    merged = ExtractResult()
    present: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    for comp in components:
        dir_ = shard_dir(root, comp)
        manifest = dir_ / MANIFEST
        if not manifest.exists():
            missing.append(comp["name"])
            continue
        # A damaged manifest is a shard whose digest cannot be checked, and an
        # unverifiable shard is exactly what `mismatched` already means. It
        # used to raise instead — and `snapshot.json` is COMMITTED and diffed
        # in pull requests, so a conflict marker left in one took the build
        # down with a JSONDecodeError rather than naming the component.
        recorded = read_manifest(dir_)
        if recorded is None or _digest(dir_) != recorded.get("sha256", ""):
            mismatched.append(comp["name"])
            continue
        try:
            shard = emit.read_jsonl(dir_ / SHARD)
        except (emit.ShardSchemaError, emit.ShardRowError):
            # NAMED, not `except ValueError` (round 4 correction): a bare `ValueError`/
            # `TypeError` catch here does not distinguish a deliberate row refusal from an
            # unrelated bug in `read_jsonl` that happens to raise the same built-in type -
            # measured, one nonsense line turned all seven of a workspace's GOOD shards
            # into "does not match its own recorded digest, rebuild and re-publish it",
            # which would have overwritten every one of them. `ShardSchemaError` and
            # `ShardRowError` are the two things `emit` raises ON PURPOSE; anything else
            # is a bug and now reaches a person as the traceback a bug should be.
            mismatched.append(comp["name"])
            continue
        unresolved_path = dir_ / SHARD_UNRESOLVED
        if unresolved_path.exists():
            shard.unresolved = [json.loads(line) for line in
                                unresolved_path.read_text(encoding="utf-8").splitlines()
                                if line.strip()]
        merged.extend(shard)
        present.append(comp["name"])
    return merged, present, missing, mismatched


def check(root: Path, components: list[dict],
          facts_by_component: dict[str, ExtractResult]
          ) -> tuple[list[str], list[str]]:
    """Rebuild == snapshot, per shard. Returns (drifted, unpublished).

    Serializes the fresh facts through the same emitter the shards were written with and
    byte-compares — a second serializer here would be a second chance to serialize
    differently, which is the drift this check exists to catch. Per-shard on purpose: a
    drifting extractor localises to the component whose files it read.

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
    with tempfile.TemporaryDirectory(prefix="sdlc-snapshot-check-") as tmp:
        scratch = Path(tmp)
        for comp in components:
            committed = shard_dir(root, comp)
            if not (committed / MANIFEST).exists():
                unpublished.append(comp["name"])
                continue
            fresh = scratch / comp["name"]
            write_shard(fresh, facts_by_component[comp["name"]])
            if _digest(fresh) != _digest(committed):
                drifted.append(comp["name"])
    return drifted, unpublished
