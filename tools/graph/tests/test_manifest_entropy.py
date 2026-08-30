"""The committed manifest must not ship a wall of digests. Item 102.

`.sdlc/graph/` is COMMITTED on purpose — it is how the next clone inherits a
baseline instead of rebuilding one and hoping it matches. So everything the
build writes there ships into every adopter's repository, and gets scanned by
their secret gate on the very first commit.

**Measured on a real install: 7,502 per-file sha1 digests, 1.34 MB, and 269
gitleaks findings** — all `generic-api-key`, all of them 40-hex content digests,
zero credential material anywhere among them. `prepass.py`'s own docstring names
the consequence: an unusable check gets switched off. The only ways past were to
exclude gitleaks or to exclude `.sdlc/`, and `.sdlc/` is where the profile, the
policy and the ledger live.

**The map was read by nothing.** `Manifest.changed_since` was its only consumer
and was called from no production code; `build.input_digests` + `stamp_matches`
do the same job per extractor and are what the freshness check reads. So this is
not a scanner problem worked around — it is dead weight removed, and these tests
hold it removed.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import manifest as M  # noqa: E402

#: What a secret scanner's entropy rules see. Not "sha1" — the rule does not
#: know what the string means, only that it is a long unbroken run of hex.
HEXISH = re.compile(r"\b[0-9a-f]{32,}\b")


# ── the map is gone, and stays gone ─────────────────────────────────────────


def test_the_manifest_carries_no_per_file_digest_map():
    """**The regression.** Red on the pre-fix arrangement, where `files` was a
    dataclass field written on every build."""
    named = {f.name for f in fields(M.Manifest)}
    assert "files" not in named, (
        "the per-file digest map is back: it is read by nothing, and it ships "
        "40-hex strings into every adopter's committed graph, where the secret "
        "gate reports them as API keys")
    assert not hasattr(M.Manifest(), "files")


def test_nothing_can_write_one_either():
    """The field alone is not the mechanism. `record_files` filled it and
    `changed_since` read it; leaving either behind is leaving the way back."""
    for gone in ("record_files", "changed_since"):
        assert not hasattr(M.Manifest, gone), f"{gone} is back"


def test_the_build_writes_no_digest_map():
    """Asserted on the source, because the field could be re-added anywhere and
    the build is where it was filled."""
    text = (ROOT / "tools" / "graph" / "build.py").read_text(encoding="utf-8")
    assert "record_files" not in text


def test_a_saved_manifest_holds_no_long_hex_run_but_the_commit_stamp(tmp_path):
    """**What actually reaches an adopter's repository**, checked by writing one
    and reading the BYTES rather than by reasoning about the shape.

    Driven through load→save of a manifest that CARRIES the old map, because
    that is what an upgrade does and because a freshly-constructed one would
    pass this assertion on the pre-fix source too — the digest map is only
    populated by a build. Round-tripping an old one discriminates: pre-fix it is
    kept and re-saved, and the hex wall comes straight back out.

    A repo HEAD is itself 40 hex characters and it STAYS: one line per
    component, the provenance the freshness check needs. One stamp is not a
    wall. The defect was 7,502 of them.
    """
    old = {"schema_version": 1, "run_id": "abc", "extractor_version": "v1",
           "repos": {"api": {"alias": "api", "head": "a" * 40, "dirty": False,
                             "branch": "main"}},
           "files": {f"api#properties/{i}.properties": f"{i:x}" * 40
                     for i in range(3)},
           "counts": {"properties": 12}}
    source = tmp_path / "old.json"
    source.write_text(json.dumps(old), encoding="utf-8")

    path = tmp_path / "manifest.json"
    M.Manifest.load(source).save(path)
    body = path.read_text(encoding="utf-8")
    found = HEXISH.findall(body)
    assert found == ["a" * 40], f"unexpected long hex runs: {found[:5]}"
    assert json.loads(body)["repos"]["api"]["head"] == "a" * 40


def test_the_saved_manifest_is_small_enough_to_commit():
    """1.34 MB was the measurement that made this a defect rather than a smell.
    A manifest for one component is a page, not a megabyte."""
    m = M.Manifest(run_id="abc", extractor_version="v1",
                   repos={"api": M.RepoStamp(alias="api", head="a" * 40)},
                   counts={f"extractor-{i}": i for i in range(20)})
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        m.save(path)
        assert path.stat().st_size < 4096


# ── what a fresh clone can still do with one ────────────────────────────────


def test_an_old_manifest_that_still_carries_the_map_is_read_not_refused():
    """**Every manifest committed before this change carries `files`.**

    `cls(**raw)` raises TypeError on an unexpected keyword, so without this an
    upgrade would break reading the graph an adopter already has - the fix
    becoming the outage. Dropped rather than rejected: it is a cache nothing
    reads, and refusing a whole manifest for carrying one would be worse than
    the noise it caused.
    """
    import tempfile

    old = {"schema_version": 1, "run_id": "r", "extractor_version": "v1",
           "repos": {"api": {"alias": "api", "head": "b" * 40, "dirty": False,
                             "branch": "main"}},
           "files": {"api#properties/a.properties": "c" * 40},
           "counts": {"properties": 3}}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(old), encoding="utf-8")
        back = M.Manifest.load(path)
    assert back.repos["api"].head == "b" * 40
    assert back.counts == {"properties": 3}
    assert not hasattr(back, "files")


def test_the_freshness_stamp_still_works_without_the_map():
    """What the manifest is FOR survives: a moved HEAD is still visible, which
    is what refuses a context pack computed from a stale graph."""
    m = M.Manifest(repos={"api": M.RepoStamp(alias="api", head="a" * 40)})
    assert m.stale_repos({"api": "a" * 40}) == []
    assert m.stale_repos({"api": "d" * 40}) == ["api"]


def test_the_incremental_decision_that_replaced_it_is_still_wired():
    """The map is removable only because something else does the job. If these
    go, the deletion stops being safe and this says so."""
    assert callable(B.input_digests) and callable(B.stamp_matches)
    assert callable(M.file_digest), "input_digests digests content through this"
    assert not B.stamp_matches("unreadable-x", "unreadable-x")
