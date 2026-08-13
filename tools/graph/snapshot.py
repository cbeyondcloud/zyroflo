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


def _digest(dir_: Path) -> str:
    """sha256 over the shard's two files. Missing files hash as empty, so a shard that
    lost its unresolved half reads as changed, not as clean."""
    h = hashlib.sha256()
    for name in (SHARD, SHARD_UNRESOLVED):
        p = dir_ / name
        h.update(p.read_bytes() if p.exists() else b"")
        h.update(b"\x00")
    return h.hexdigest()


def write_shard(dir_: Path, facts: ExtractResult) -> str:
    """Write one component's facts and stamp the manifest. Returns the digest."""
    emit.write_jsonl(dir_ / SHARD, facts, extractor="shard")
    emit.write_unresolved(dir_ / SHARD_UNRESOLVED, facts.unresolved)
    digest = _digest(dir_)
    (dir_ / MANIFEST).write_text(json.dumps(
        {"schema_version": 1, "sha256": digest}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return digest


def publish(root: Path, components: list[dict],
            facts_by_component: dict[str, ExtractResult]) -> dict[str, str]:
    """Write every component's shard. Returns {component: digest}."""
    return {comp["name"]: write_shard(shard_dir(root, comp),
                                      facts_by_component[comp["name"]])
            for comp in components}


def load(root: Path, components: list[dict]
         ) -> tuple[ExtractResult, list[str], list[str], list[str]]:
    """Union the committed shards. Returns (facts, present, missing, mismatched).

    `mismatched` names components whose shard bytes no longer match their recorded
    digest — a hand edit, a half-written publish, a merge gone wrong. Their rows are NOT
    loaded: a shard that fails its own manifest is evidence about nothing.
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
        recorded = json.loads(manifest.read_text(encoding="utf-8")).get("sha256", "")
        if _digest(dir_) != recorded:
            mismatched.append(comp["name"])
            continue
        shard = emit.read_jsonl(dir_ / SHARD)
        unresolved_path = dir_ / SHARD_UNRESOLVED
        if unresolved_path.exists():
            shard.unresolved = [json.loads(line) for line in
                                unresolved_path.read_text(encoding="utf-8").splitlines()
                                if line.strip()]
        merged.extend(shard)
        present.append(comp["name"])
    return merged, present, missing, mismatched


def check(root: Path, components: list[dict],
          facts_by_component: dict[str, ExtractResult], scratch: Path
          ) -> tuple[list[str], list[str]]:
    """Rebuild == snapshot, per shard. Returns (drifted, unpublished).

    Serializes the fresh facts through the same emitter the shards were written with and
    byte-compares — a second serializer here would be a second chance to serialize
    differently, which is the drift this check exists to catch. Per-shard on purpose: a
    drifting extractor localises to the component whose files it read.
    """
    drifted: list[str] = []
    unpublished: list[str] = []
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
