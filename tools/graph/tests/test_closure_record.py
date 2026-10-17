"""Batch 22 (T632; design pass 22, section 1): a shard records the cross-component inputs it read
- `closure_inputs: {component: {extractor: digest}}`, one digest per (component, extractor) for
each extractor in its cross-component closure and each OTHER component where it is active - and
claim 9 judges what the dependent itself read, its record against what the record would be now:

- 9a, a recorded pair with a digest now that no longer matches: "depends on <e>, whose inputs in
  <c> changed since its shard was published" - or, a side poisoned, "cannot be read now (make
  them readable)" (the digest now) or "could not be read when its shard was published" (the
  record's alone);
- 9b, a recorded pair with no digest now: "depends on <e>, which is no longer active in <c>";
- 9c, a pair with a digest now and no record: "depends on <e>, now active in <c>, which its
  shard did not read";

in that order, components and extractors in registry order, the first as the reason. A
sibling's own state - its stamps, its shard - is never read: the dependent's facts depend on
the sibling's FILES. Measured on `main` (T632): `a`'s value changed and `a` re-published while
`b` did not - `--check-fresh` exit 0, both current, every load serving `b`'s old topic.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness as F, snapshot as SN  # noqa: E402
from tools.graph.core.model import ExtractResult  # noqa: E402
from tools.graph.tests import _unreadable as U  # noqa: E402
from tools.graph.tests._estate import build, compose, lay_down, producer, spawn, svc  # noqa: E402

#: `b`'s producer reads `a`'s compose file through the shared cache (`env-keys` requires
#: `compose-services`, a shared producer), so `b`'s record holds `a`'s compose digest.
FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
TWO = {**FEED, "c": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))}}
DROP = object()
POISON = "unreadable-" + "0" * 40


class World:
    """A laid-down tree judged with the real registry, by the cross-component closure the build
    passes the verdict; `current(name)` is the reading a publish of this tree now would leave."""

    def __init__(self, where: Path, estate: dict):
        lay_down(where, estate)
        self.root = where / "ws"
        self.components = [{"name": c, "path": c} for c in estate]
        self.regs = B.discover_extractors()
        self.act = B.activate(self.regs, self.components, self.root,
                              workspace=self.components)
        self.closure = B.cross_closure(self.regs)
        self.parser_of = B.parser_rule(self.regs)
        self.digests = B.input_digests(self.act, self.components, self.root)
        self.version = B.extractor_version(self.regs)
        self.config = {c["name"]: "shared:own" for c in self.components}

    def record(self, name: str) -> dict:
        return F.closure_record(name, self.act, self.closure, self.digests)

    def current(self, name: str, **stamps) -> SN.ShardReading:
        recorded = {"inputs": dict(self.digests.get(name, {})),
                    "extractor_version": self.version, "config_sha": self.config[name],
                    "sha256": "0" * 64,
                    "parsers": F.parser_stamp(name, self.act, self.closure, lambda p: True,
                                              self.parser_of, self.root, self.components,
                                              version=lambda p: "installed"),
                    "closure_inputs": self.record(name), **stamps}
        return SN.ShardReading(stamps={k: v for k, v in recorded.items() if v is not DROP},
                               facts=ExtractResult())

    def judge(self, **readings: SN.ShardReading) -> dict[str, F.Verdict]:
        full = {c["name"]: readings[c["name"]] if c["name"] in readings
                else self.current(c["name"]) for c in self.components}
        return F.judge(full, self.components, self.act, self.closure, self.digests,
                       self.version, self.config, lambda p: True, self.parser_of, self.root,
                       parser_version=lambda p: "installed")


@pytest.fixture
def feed(tmp_path) -> World:
    return World(tmp_path, FEED)


def stale(words: str) -> F.Verdict:
    return F.Verdict("stale", words)


def with_digest(world: World, comp: str, ext: str, digest: str | None) -> None:
    """The digest now of `ext` in `comp` - None where `ext` is no longer active there."""
    per = dict(world.digests[comp])
    if digest is None:
        per.pop(ext)
    else:
        per[ext] = digest
    world.digests = {**world.digests, comp: per}


# ── the record ─────────────────────────────────────────────────────────────────────────────


def test_the_record_holds_each_other_components_digest_through_the_cross_closure(feed):
    assert feed.record("b") == {"a": {"compose-services": feed.digests["a"]["compose-services"]}}
    assert feed.record("a") == {}, "a's readers depend on nothing in another component"


def test_the_record_leaves_the_dependents_own_component_out(tmp_path):
    """Its own inputs, closure members included, are in `inputs` already: `b` composing a
    service of its own records `a`'s compose digest alone."""
    world = World(tmp_path, {**FEED, "b": {**FEED["b"],
                                           "docker-compose.yml": compose(svc("z"))}})
    assert "compose-services" in world.digests["b"]
    assert world.record("b") == {"a": {"compose-services":
                                       world.digests["a"]["compose-services"]}}


def test_the_reason_follows_the_registry_order_of_components_not_their_names(tmp_path):
    """`z` is declared before `a`: both moved, `z` is the line's reason."""
    world = World(tmp_path, {"z": TWO["c"], "b": TWO["b"], "a": TWO["a"]})
    recorded = world.record("b")
    with_digest(world, "a", "compose-services", "1" * 40)
    with_digest(world, "z", "compose-services", "2" * 40)
    assert world.judge(b=world.current("b", closure_inputs=recorded))["b"] == stale(
        "depends on compose-services, whose inputs in z changed since its shard was published")


def test_a_current_record_leaves_the_dependent_current(feed):
    assert feed.judge() == {"a": F.Verdict("current"), "b": F.Verdict("current")}


# ── 9a, 9b, 9c ─────────────────────────────────────────────────────────────────────────────


def test_9a_a_sibling_input_changed_since_the_dependents_publish(feed):
    old = feed.record("b")
    with_digest(feed, "a", "compose-services", "1" * 40)
    assert feed.judge(b=feed.current("b", closure_inputs=old))["b"] == stale(
        "depends on compose-services, whose inputs in a changed since its shard was published")


@pytest.mark.parametrize("then, now, words", [
    (None, POISON, "depends on compose-services, whose inputs in a cannot be read now (make "
                   "them readable)"),
    (POISON, None, "depends on compose-services, whose inputs in a could not be read when its "
                   "shard was published"),
    (POISON, POISON, "depends on compose-services, whose inputs in a cannot be read now (make "
                     "them readable)"),
], ids=["the-digest-now", "only-the-record", "both"])
def test_9a_words_a_poisoned_side_for_what_it_is(feed, then, now, words):
    """D22-73b: a poisoned digest is never a match, and says so rather than "changed"."""
    recorded = {"a": {"compose-services": then or feed.digests["a"]["compose-services"]}}
    if now:
        with_digest(feed, "a", "compose-services", now)
    assert feed.judge(b=feed.current("b", closure_inputs=recorded))["b"] == stale(words)


def test_9a_unreadable_both_sides_needs_a_publish_after_the_file_reads(feed):
    """D22-84b, D22-91: readable again, the line moves to the record's rendering; a publish
    after the file reads clears it."""
    readable = feed.digests["a"]["compose-services"]
    recorded = {"a": {"compose-services": POISON}}
    with_digest(feed, "a", "compose-services", POISON)
    assert feed.judge(b=feed.current("b", closure_inputs=recorded))["b"].words.endswith(
        "cannot be read now (make them readable)")
    with_digest(feed, "a", "compose-services", readable)
    assert feed.judge(b=feed.current("b", closure_inputs=recorded))["b"] == stale(
        "depends on compose-services, whose inputs in a could not be read when its shard was "
        "published")
    assert feed.judge()["b"] == F.Verdict("current"), "re-published once the file reads"


def test_9a_unreadable_now_follows_its_own_way_on(feed):
    """Once the file reads, unchanged, the pair matches with no publish."""
    recorded = feed.record("b")
    with_digest(feed, "a", "compose-services", POISON)
    assert feed.judge(b=feed.current("b", closure_inputs=recorded))["b"].state == "stale"
    with_digest(feed, "a", "compose-services", recorded["a"]["compose-services"])
    assert feed.judge(b=feed.current("b", closure_inputs=recorded))["b"] == F.Verdict("current")


def test_9b_a_recorded_extractor_no_longer_active_in_its_component(feed):
    """D16-52's emptied sibling: 9a asks only of a pair with a digest now, so it never takes
    9b's case (D22-31)."""
    recorded = feed.record("b")
    with_digest(feed, "a", "compose-services", None)
    assert feed.judge(b=feed.current("b", closure_inputs=recorded))["b"] == stale(
        "depends on compose-services, which is no longer active in a")


def test_9c_an_extractor_now_active_where_the_shard_read_nothing(feed):
    assert feed.judge(b=feed.current("b", closure_inputs={}))["b"] == stale(
        "depends on compose-services, now active in a, which its shard did not read")


def test_9a_comes_before_9b_and_9b_before_9c_in_component_order(tmp_path):
    world = World(tmp_path, TWO)
    recorded = world.record("b")
    assert set(recorded) == {"a", "c"}
    with_digest(world, "a", "compose-services", None)          # 9b in a
    with_digest(world, "c", "compose-services", "1" * 40)      # 9a in c
    assert world.judge(b=world.current("b", closure_inputs=recorded))["b"] == stale(
        "depends on compose-services, whose inputs in c changed since its shard was published")
    world = World(tmp_path / "again", TWO)
    both = world.record("b")
    with_digest(world, "a", "compose-services", "1" * 40)
    with_digest(world, "c", "compose-services", "2" * 40)
    assert world.judge(b=world.current("b", closure_inputs=both))["b"].words.endswith(
        "whose inputs in a changed since its shard was published"), "a first: registry order"
    world = World(tmp_path / "third", TWO)
    only_c = {"c": world.record("b")["c"]}
    with_digest(world, "c", "compose-services", None)          # 9b in c, 9c in a
    assert world.judge(b=world.current("b", closure_inputs=only_c))["b"] == stale(
        "depends on compose-services, which is no longer active in c")


@pytest.mark.parametrize("reading", [
    SN.ShardReading(absent="no shard"), SN.ShardReading(absent="no stamps"),
    SN.ShardReading(refused="its committed snapshot.json exists but could not be read (not "
                            "JSON)", remedy="re-publish", refusal_kind="manifest"),
], ids=["no-shard", "no-stamps", "manifest-unreadable"])
def test_a_siblings_own_state_is_never_read(feed, reading):
    """`main`'s three `_UNCOMPARABLE` reasons are dropped (decision 48): the record compares
    the sibling's files, which have not moved."""
    assert feed.judge(a=reading)["b"] == F.Verdict("current")


def test_a_sibling_stale_on_its_own_leaves_the_dependent_current_where_its_files_did_not_move(
        feed):
    """A sibling whose own stamp differs - edited after its publish, before the dependent's -
    leaves the dependent current: the dependent read the edited files."""
    moved = feed.current("a", inputs={**feed.digests["a"], "compose-services": "0" * 40})
    assert feed.judge(a=moved)["b"] == F.Verdict("current")


def test_an_old_shard_with_no_record_is_stale_where_it_would_hold_one(feed):
    """D22-12: reachable through a hand-edited manifest - claim 4 stales every shard published
    before the upgrade first."""
    verdicts = feed.judge(a=feed.current("a", closure_inputs=DROP),
                          b=feed.current("b", closure_inputs=DROP))
    assert verdicts["b"] == stale(
        "its shard was published before it recorded the inputs it read from other components")
    assert verdicts["a"] == F.Verdict("current"), "a's record would be empty"


def test_claim_8_comes_before_claim_9(tmp_path):
    """`b` composes a service of its own, so YAML is in its own half too."""
    world = World(tmp_path, {**FEED, "b": {**FEED["b"],
                                           "docker-compose.yml": compose(svc("z"))}})
    stamp = world.current("b").stamps["parsers"]
    assert "yaml" in stamp and world.record("b")
    old = {"a": {"compose-services": "1" * 40}}
    verdict = world.judge(b=world.current("b", closure_inputs=old,
                                          parsers={**stamp, "yaml": False}))["b"]
    assert verdict.words.startswith("its shard was published while `compose-services` had no "
                                    "parser installed"), verdict


def test_claim_9_comes_before_claim_10(feed):
    """`b`'s YAML is its closure half alone (claim 10)."""
    old = {"a": {"compose-services": "1" * 40}}
    verdict = feed.judge(b=feed.current("b", closure_inputs=old, parsers={"yaml": False}))["b"]
    assert verdict == stale("depends on compose-services, whose inputs in a changed since its "
                            "shard was published")
    assert feed.judge(b=feed.current("b", parsers={"yaml": False}))["b"].words.startswith(
        "depends on compose-services, which had no parser installed"), "claim 10 behind it"


# ── the record's seams ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("record, fault", [
    ({"a": {"compose-services": "abc"}}, None),
    ({}, None),
    ({"a": "abc"}, "`closure_inputs` is not a mapping of components to digests"),
    (["a"], "`closure_inputs` is not a mapping of components to digests"),
    ({"a": {"compose-services": 7}}, "`closure_inputs` is not a mapping of components to digests"),
], ids=["well-formed", "empty", "a-component-to-a-string", "a-list", "a-digest-not-a-string"])
def test_the_record_is_typed_by_the_reading(record, fault):
    assert SN._stamp_block_fault({"sha256": "0" * 64, "closure_inputs": record}) == fault


def test_stamps_of_carries_the_record_where_the_manifest_has_it():
    base = {"sha256": "x", "inputs": {}, "extractor_version": "v", "config_sha": "c"}
    assert SN.stamps_of({**base, "closure_inputs": {"a": {"e": "d"}}})["closure_inputs"] == {
        "a": {"e": "d"}}
    assert "closure_inputs" not in SN.stamps_of(base), "absent is the old shard, never {}"


def test_the_publish_writes_exactly_what_the_one_producer_computes(tmp_path, monkeypatch):
    seen: dict[str, dict] = {}
    real = F.closure_record

    def recording(component, *args, **kwargs):
        seen[component] = real(component, *args, **kwargs)
        return seen[component]
    monkeypatch.setattr(F, "closure_record", recording)
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    assert set(seen) == {"a", "b"}
    for comp in seen:
        manifest = json.loads((tmp_path / "ws" / comp / SN.SHARD_DIR / SN.MANIFEST)
                              .read_text(encoding="utf-8"))
        assert manifest["closure_inputs"] == seen[comp]
    assert seen["a"] == {} and set(seen["b"]) == {"a"}


def test_carries_way_names_the_unreadable_now_rendering_alone():
    assert F.carries_way("depends on compose-services, whose inputs in a cannot be read now "
                         "(make them readable)")
    assert not F.carries_way("depends on compose-services, whose inputs in a changed since its "
                             "shard was published")


# ── T632 through the doors ─────────────────────────────────────────────────────────────────


def topics(out: Path) -> set[str]:
    """The topic nodes a graph holds - the value `b`'s producer resolved."""
    found = set()
    for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if str(row.get("id", "")).startswith("topic:"):
                found.add(row["id"])
    return found


def test_a_dependent_whose_sibling_re_published_alone_is_stale_at_every_door(tmp_path):
    """T632's probe cell, each repository committing its own shard: `a`'s value changes and
    `a` re-publishes; `b`'s repository does not."""
    lay_down(tmp_path, FEED)
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    b_shard = tmp_path / "ws" / "b" / SN.SHARD_DIR
    kept = tmp_path / "b-shard"
    shutil.copytree(b_shard, kept)
    (tmp_path / "ws" / "a" / "docker-compose.yml").write_text(
        compose(svc("b", "../b", "web-value-2")), encoding="utf-8")
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    shutil.rmtree(b_shard)
    shutil.copytree(kept, b_shard)                       # b's repository never re-published
    reason = ("depends on compose-services, whose inputs in a changed since its shard was "
              "published")

    fresh = spawn(tmp_path, "--check-fresh", out="fresh")
    assert fresh.returncode == 2, fresh.stdout + fresh.stderr
    assert [line for line in fresh.stdout.splitlines() if line.startswith("freshness: STALE")] \
        == [f"freshness: STALE b - {reason}"]

    load = spawn(tmp_path, "--from-snapshot", out="load")
    assert load.returncode == 0, load.stdout + load.stderr
    assert topics(tmp_path / "load") == {"topic:web-value"}, "served as published"
    report = json.loads((tmp_path / "load" / "build_report.json").read_text(encoding="utf-8"))
    (note,) = report["notes"]
    assert f"stale - {reason}: b" in note
    assert note.endswith("To judge them: re-publish the shards it names from a build of the "
                         "workspace (`--check-fresh` then exits 0), or build the whole "
                         "workspace."), "main's way on: the words carry none of their own"

    inc = spawn(tmp_path, "--incremental", "--publish-snapshot", out="inc")
    assert inc.returncode == 0, inc.stdout + inc.stderr
    plan = json.loads((tmp_path / "inc" / "build_report.json")
                      .read_text(encoding="utf-8"))["incremental"]
    assert plan["rebuilt"] == {"b": reason} and plan["reused"] == ["a"]
    assert spawn(tmp_path, "--check-fresh", out="fresh").returncode == 0
    assert spawn(tmp_path, "--from-snapshot", out="load2").returncode == 0
    assert topics(tmp_path / "load2") == {"topic:web-value-2"}, "the full build's topic"


def test_an_emptied_sibling_says_its_reader_left(tmp_path):
    """D16-52: the sibling's compose file removed - its directory otherwise empty - so the
    dependent's record names a pair with no digest now (9b)."""
    lay_down(tmp_path, FEED)
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    (tmp_path / "ws" / "a" / "docker-compose.yml").unlink()
    fresh = spawn(tmp_path, "--check-fresh", out="fresh")
    assert fresh.returncode == 2, fresh.stdout + fresh.stderr
    assert ("freshness: STALE b - depends on compose-services, which is no longer active in a"
            in fresh.stdout.splitlines()), fresh.stdout


def test_a_malformed_record_is_refused_with_the_re_publish_remedy(tmp_path):
    lay_down(tmp_path, FEED)
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    manifest = tmp_path / "ws" / "b" / SN.SHARD_DIR / SN.MANIFEST
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    recorded["closure_inputs"] = ["a"]
    manifest.write_text(json.dumps(recorded), encoding="utf-8")
    fresh = spawn(tmp_path, "--check-fresh", out="fresh")
    assert fresh.returncode == 2, fresh.stdout + fresh.stderr
    assert ("freshness: REFUSED b - its committed snapshot.json exists but could not be read "
            "(`closure_inputs` is not a mapping of components to digests); re-publish (build "
            "--publish-snapshot)") in fresh.stdout.splitlines(), fresh.stdout
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0, "the remedy, followed"
    assert spawn(tmp_path, "--check-fresh", out="fresh").returncode == 0


def test_a_load_offers_no_way_on_beside_words_that_carry_their_own(tmp_path):
    """D22-79: `a`'s compose file unreadable - `a` stale by its own files, `b` by 9a's
    unreadable-now rendering, whose way on is in its words - and the load's line prints each
    way on once: the re-publish for `a`'s group alone."""
    with U.site(tmp_path) as where:
        lay_down(where, FEED)
        assert spawn(where, "--publish-snapshot").returncode == 0
        U.hand_over(where)
        target = where / "ws" / "a" / "docker-compose.yml"
        with U.unreadable(target):
            load = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "graph" / "build.py"),
                 "--workspace", str(where / "ws" / "workspace.toml"), "--out",
                 str(where / "load"), "--run-id", "fixed", "--empty-ok", "--from-snapshot"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(ROOT), **U.door_kwargs())
        assert load.returncode == 0, load.stdout + load.stderr
        report = json.loads((where / "load" / "build_report.json").read_text(encoding="utf-8"))
        (note,) = report["notes"]
        assert ("stale - depends on compose-services, whose inputs in a cannot be read now (make "
                "them readable): b") in note, note
        assert note.count("re-publish the shards it names") == 1, note
        assert note.endswith("or build the whole workspace."), note
