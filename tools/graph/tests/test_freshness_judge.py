"""Batch 16 (T627, T629, T630; the owner's rulings 27-30): one verdict for every shard, by the
design note's claims in order - the first that fails is the verdict (section 4).

Synthetic readings over real activations: what each reading holds is set here; which readers
are active where, which files they read and which parser each file needs come from the real
registry, so a cell's words are the words a person would read for that tree. The reader lists
below are measured, in registry order.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness as F, snapshot as SN  # noqa: E402
from tools.graph.core.model import ExtractResult  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, producer, svc  # noqa: E402

#: The kept words (ruling 29), as `main` prints them.
MISSING = "no freshness stamps — no committed shard, or one published before stamps existed"
EXTRACTORS_CHANGED = "the extractor set changed since its shard was published"
STALE_CONFIG = "the workspace configuration changed since its shard was published"
NO_PARSER_STAMP = "its shard was published before parser stamps existed"

#: `a`'s compose file is read by `compose-services` (YAML, `a`'s own half); `b`'s producer
#: reads it through the shared cache, so `b`'s closure half reaches YAML (measured).
FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
#: `x` adds Java its own readers parse - the own half - to `b`'s closure half.
JAVA = {"a": {"docker-compose.yml": compose(svc("x", "../x", "web-value"))},
        "x": {"app.py": producer("TOPIC"), "src/main/java/Api.java": "class Api {}\n"}}
JAVA_READERS = "`kafka-consumers`, `kafka-producers`, `spring-call-concats`, `spring-controllers`"
#: One component whose own half reaches two parsers.
BOTH = {"m": {"docker-compose.yml": compose(svc("x", "../x", "web-value")),
              "src/main/java/Api.java": "class Api {}\n"}}

DROP = object()


class World:
    """A laid-down tree, judged with the real registry; `missing` is the parsers not installed
    now, and `current(name)` is the reading a publish of this tree now would leave."""

    def __init__(self, where: Path, estate: dict):
        lay_down(where, estate)
        self.root = where / "ws"
        self.components = [{"name": c, "path": c} for c in estate if not c.startswith("__")]
        self.regs = B.discover_extractors()
        self.act = B.activate(self.regs, self.components, self.root,
                              workspace=self.components)
        self.closure = B.requires_closure(self.regs)
        self.parser_of = B.parser_rule(self.regs)
        self.digests = B.input_digests(self.act, self.components, self.root)
        self.version = B.extractor_version(self.regs)
        self.config = {c["name"]: "shared:own" for c in self.components}
        self.missing: set[str] = set()

    def probe(self, parser: str) -> bool:
        return parser not in self.missing

    def stamp(self, name: str) -> dict[str, bool]:
        return F.parser_stamp(name, self.act, self.closure, self.probe, self.parser_of,
                              self.root, self.components)

    def current(self, name: str, **stamps) -> SN.ShardReading:
        recorded = {"inputs": dict(self.digests.get(name, {})),
                    "extractor_version": self.version, "config_sha": self.config[name],
                    "sha256": "0" * 64, "parsers": self.stamp(name), **stamps}
        return SN.ShardReading(stamps={k: v for k, v in recorded.items() if v is not DROP},
                               facts=ExtractResult())

    def moved(self, name: str, reader: str) -> SN.ShardReading:
        """A reading whose stamp says `reader`'s files in `name` were other bytes."""
        return self.current(name, inputs={**self.digests[name], reader: "0" * 40})

    def judge(self, **readings: SN.ShardReading) -> dict[str, F.Verdict]:
        full = {c["name"]: readings[c["name"]] if c["name"] in readings
                else self.current(c["name"]) for c in self.components}
        return F.judge(full, self.components, self.act, self.closure, self.digests,
                       self.version, self.config, self.probe, self.parser_of, self.root)


@pytest.fixture
def feed(tmp_path) -> World:
    return World(tmp_path, FEED)


def stale(words: str) -> F.Verdict:
    return F.Verdict("stale", words)


# ── the claims, in order ───────────────────────────────────────────────────────────────────


def test_claim_11_a_shard_nothing_contradicts_is_current(feed):
    assert feed.judge() == {"a": F.Verdict("current"), "b": F.Verdict("current")}


def test_claim_1_a_component_with_no_shard_is_missing(feed):
    assert feed.judge(a=SN.ShardReading(absent="no shard"))["a"] == F.Verdict("missing",
                                                                               MISSING)


def test_claim_2_a_refused_reading_keeps_its_reason_remedy_and_load_words(feed):
    reading = feed.current("a")
    reading.refused, reading.refused_load = SN.DIGEST_WORDS, SN.DIGEST_LOAD_WORDS
    reading.remedy, reading.refusal_kind = "re-publish", "digest"
    verdict = feed.judge(a=reading)["a"]
    assert verdict == F.Verdict("refused", SN.DIGEST_WORDS, load_words=SN.DIGEST_LOAD_WORDS,
                                remedy="re-publish")
    assert (verdict.load, verdict.plan) == (SN.DIGEST_LOAD_WORDS, SN.DIGEST_WORDS)


def test_claim_3_a_shard_with_no_stamps_is_stale_in_the_kept_words(feed):
    assert feed.judge(a=SN.ShardReading(absent="no stamps"))["a"] == stale(MISSING)


def test_claim_4_a_changed_extractor_set(feed):
    assert feed.judge(a=feed.current("a", extractor_version="0"))["a"] == stale(
        EXTRACTORS_CHANGED)


def test_claim_5_a_changed_configuration_either_half(feed):
    for changed in ("shared:other", "other:own"):
        assert feed.judge(a=feed.current("a", config_sha=changed))["a"] == stale(STALE_CONFIG)


def test_claim_6_files_a_reader_reads_moved_in_both_renderings(feed):
    verdict = feed.judge(a=feed.moved("a", "compose-services"))["a"]
    assert verdict == F.Verdict("stale", "files read by compose-services changed",
                                plan_words="its own files changed")
    assert verdict.load == "files read by compose-services changed"


def test_claim_6_names_each_case_record_in_its_own_words(feed):
    reading = feed.current("a", inputs={**feed.digests["a"], "compose-services": "0" * 40,
                                        F.ACTIVATION: "1" * 40, F.ACTIVATION_DEPLOY: "2" * 40})
    assert feed.judge(a=reading)["a"].words == (
        "files read by compose-services changed; files no extractor reads because of their "
        "case changed; a file a deployment reader misses only by case changed")


def test_claim_7_a_shard_published_before_parser_stamps(feed):
    assert feed.judge(a=feed.current("a", parsers=DROP))["a"] == stale(NO_PARSER_STAMP)


@pytest.mark.parametrize("stamped, missing, words", [
    ({"yaml": False}, set(),
     "its shard was published while `compose-services` had no parser installed"),
    ({"yaml": True}, {"yaml"},
     "its shard was published while `compose-services` had a parser that is no longer "
     "installed"),
    ({}, set(),
     "its shard's parser stamp does not record `yaml`, which `compose-services` now use"),
], ids=["installed-since", "removed-since", "a-name-the-stamp-lacks"])
def test_claim_8_the_own_half_of_the_parser_stamp(feed, stamped, missing, words):
    feed.missing = missing
    assert feed.judge(a=feed.current("a", parsers=stamped))["a"] == stale(words)


def test_claim_8_joins_its_clauses_and_lists_readers_in_registry_order(tmp_path):
    world = World(tmp_path, BOTH)
    world.missing = {"yaml"}
    assert world.judge(m=world.current("m", parsers={"java": False, "yaml": True}))["m"] == (
        stale(f"its shard was published while {JAVA_READERS} had no parser installed; its "
              f"shard was published while `compose-services` had a parser that is no longer "
              f"installed"))
    world.missing = set()
    assert world.judge(m=world.current("m", parsers={"java": False, "yaml": False}))["m"] == (
        stale(f"its shard was published while `compose-services`, {JAVA_READERS} had no "
              f"parser installed"))


def test_reader_lists_follow_the_registry_not_the_alphabet(tmp_path):
    """`openapi-specs` registers before the Java readers and sorts after two of them, so only
    registry order puts it first - in claim 6's list as in claim 8's (measured)."""
    spec = "openapi: 3.0.0\ninfo:\n  title: t\n  version: '1'\npaths: {}\n"
    world = World(tmp_path, {"s": {"openapi.yaml": spec,
                                   "src/main/java/Api.java": "class Api {}\n"}})
    moved = world.current("s", inputs={**world.digests["s"], "openapi-specs": "0" * 40,
                                       "kafka-consumers": "0" * 40})
    assert world.judge(s=moved)["s"].words == (
        "files read by openapi-specs, kafka-consumers changed")
    assert world.judge(s=world.current("s", parsers={"java": False, "yaml": False}))["s"] == (
        stale(f"its shard was published while `openapi-specs`, {JAVA_READERS} had no parser "
              f"installed"))


def test_claim_9a_a_dependency_whose_inputs_moved_in_another_component(feed):
    assert feed.judge(a=feed.moved("a", "compose-services"))["b"] == stale(
        "depends on compose-services, whose inputs changed in another component")


@pytest.mark.parametrize("reading, cause", [
    (SN.ShardReading(absent="no shard"), "that has no committed shard"),
    (SN.ShardReading(absent="no stamps"), "whose shard was published before stamps existed"),
    (SN.ShardReading(refused="its committed snapshot.json exists but could not be read "
                             "(not JSON)", remedy="re-publish", refusal_kind="manifest"),
     "whose committed snapshot.json could not be read"),
], ids=["no-shard", "no-stamps", "manifest-unreadable"])
def test_claim_9b_a_dependency_active_where_nothing_can_be_compared(feed, reading, cause):
    assert feed.judge(a=reading)["b"] == stale(
        f"depends on compose-services, active in another component {cause}")


def test_claim_9b_names_the_first_cause_in_order(tmp_path):
    """`compose-services` is active in `a` (no shard) and `c` (no stamps): its cause is the
    first in order, whichever component is met last. `env-keys` is active only in `d` (no
    stamps): a later cause, so the line leaves it out."""
    estate = {**FEED, "c": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
              "d": {"app.py": producer("OTHER")}}
    world = World(tmp_path, estate)
    verdicts = world.judge(a=SN.ShardReading(absent="no shard"),
                           c=SN.ShardReading(absent="no stamps"),
                           d=SN.ShardReading(absent="no stamps"))
    assert verdicts["b"] == stale(
        "depends on compose-services, active in another component that has no committed shard")


@pytest.mark.parametrize("stamped, missing, words", [
    ({"yaml": False}, set(),
     "depends on compose-services, which had no parser installed when its shard was "
     "published"),
    ({"yaml": True}, {"yaml"},
     "depends on compose-services, whose parser was installed when its shard was published "
     "and is not now"),
    ({}, set(),
     "its shard's parser stamp does not record `yaml`, which compose-services it depends on "
     "now use"),
    ({"yaml": True, "kotlin": True}, set(),
     "its shard's parser stamp records `kotlin`, which nothing it reads uses now: what it "
     "depended on changed or left the tree"),
], ids=["installed-since", "removed-since", "a-name-the-stamp-lacks", "a-name-nothing-reaches"])
def test_claim_10_the_closure_half_of_the_parser_stamp(feed, stamped, missing, words):
    feed.missing = missing
    verdicts = feed.judge(a=feed.current("a", parsers={"yaml": not missing}),
                          b=feed.current("b", parsers=stamped))
    assert verdicts["b"] == stale(words)


def test_claim_10_joins_its_clauses(feed):
    verdicts = feed.judge(b=feed.current("b", parsers={"yaml": False, "kotlin": False}))
    assert verdicts["b"] == stale(
        "depends on compose-services, which had no parser installed when its shard was "
        "published; its shard's parser stamp records `kotlin`, which nothing it reads uses "
        "now: what it depended on changed or left the tree")


# ── precedence ─────────────────────────────────────────────────────────────────────────────


def test_a_refusal_comes_before_a_parser_stamp_that_differs(feed):
    for name, stamped in (("a", {}), ("b", {"yaml": False})):   # claims 8 and 10
        reading = feed.current(name, parsers=stamped)
        reading.refused, reading.remedy, reading.refusal_kind = (
            "its shard's rows could not be read (line 1: not JSON)", "re-publish", "rows")
        assert feed.judge(**{name: reading})[name].state == "refused"


def test_own_claims_come_before_dependents(feed):
    verdicts = feed.judge(a=feed.moved("a", "compose-services"),
                          b=feed.moved("b", "env-keys"))
    assert verdicts["b"].words == "files read by env-keys changed"


def test_claim_9a_comes_before_9b(tmp_path):
    estate = {**FEED, "c": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))}}
    world = World(tmp_path, estate)
    verdicts = world.judge(a=world.moved("a", "compose-services"),
                           c=SN.ShardReading(absent="no shard"))
    assert verdicts["b"].words == (
        "depends on compose-services, whose inputs changed in another component")


def test_claim_9_comes_before_10(feed):
    verdicts = feed.judge(a=feed.moved("a", "compose-services"),
                          b=feed.current("b", parsers={"yaml": False}))
    assert verdicts["b"].words == (
        "depends on compose-services, whose inputs changed in another component")


# ── the review focus ───────────────────────────────────────────────────────────────────────


def test_a_stamped_parser_no_reader_declares_is_stale_until_republished(feed):
    """Review focus 1: a removed profile reader's `kotlin` left in a stamp."""
    verdicts = feed.judge(a=feed.current("a", parsers={"kotlin": True, "yaml": True}))
    assert verdicts["a"] == stale(
        "its shard's parser stamp records `kotlin`, which nothing it reads uses now: what it "
        "depended on changed or left the tree")
    assert feed.judge(a=feed.current("a"))["a"] == F.Verdict("current"), "re-published"


def test_both_halves_differing_say_the_own_half_first(tmp_path):
    """Review focus 3: claim 8's words alone; claim 10's once the own half is re-published."""
    world = World(tmp_path, JAVA)
    stamped = {"java": False, "yaml": False}
    assert world.judge(x=world.current("x", parsers=stamped))["x"] == stale(
        f"its shard was published while {JAVA_READERS} had no parser installed")
    assert world.judge(x=world.current("x", parsers={**stamped, "java": True}))["x"] == stale(
        "depends on compose-services, which had no parser installed when its shard was "
        "published")


# ── `main`'s six dependent contributors ────────────────────────────────────────────────────


def test_the_three_kept_contributors_mark_a_dependent(feed):
    """The per-extractor stamp against its digest, the shared configuration, and a sibling
    with nothing to compare (`st is None`)."""
    assert feed.judge(a=feed.moved("a", "compose-services"))["b"].words.startswith(
        "depends on compose-services, whose inputs changed")
    assert feed.judge(a=feed.current("a", config_sha="old:own"))["b"].words.startswith(
        "depends on compose-services, whose inputs changed")
    assert feed.judge(a=SN.ShardReading(absent="no shard"))["b"].words.startswith(
        "depends on compose-services, active in another component")


def test_the_three_dropped_contributors_do_not(feed):
    """A sibling's extractor version, its rows saying a reader had no parser (the stamp, not
    the rows, decides currency), and its bytes failing their digest: none makes a dependent
    stale on its own (rounds 3-5)."""
    assert feed.judge(a=feed.current("a", extractor_version="0"))["b"] == F.Verdict("current")
    lost = feed.current("a")
    lost.facts.unresolved = [{"kind": "grammar-unavailable", "extractor": "compose-services",
                              "component": "a", "reason": "PyYAML is not installed"}]
    assert feed.judge(a=lost)["b"] == F.Verdict("current")
    tampered = feed.current("a")
    tampered.refused, tampered.refused_load = SN.DIGEST_WORDS, SN.DIGEST_LOAD_WORDS
    tampered.remedy, tampered.refusal_kind = "re-publish", "digest"
    assert feed.judge(a=tampered)["b"] == F.Verdict("current")


def test_a_sibling_whose_extractor_set_changed_still_counts_its_moved_inputs(feed):
    """Dropping the version contributor drops only its clause: the sibling's per-extractor
    comparisons still count, as `main` counts them after it."""
    reading = feed.current("a", extractor_version="0",
                           inputs={**feed.digests["a"], "compose-services": "0" * 40})
    assert feed.judge(a=reading)["b"] == stale(
        "depends on compose-services, whose inputs changed in another component")


# ── guard 3 ────────────────────────────────────────────────────────────────────────────────


def _registered_names() -> set[str]:
    names = {r.name for r in B.discover_extractors()}
    for profile in sorted((ROOT / "tools" / "graph" / "profiles").iterdir()):
        if (profile / "__init__.py").is_file():
            regs, links = B.discover_package(profile, "profile", {}, root_label="profiles")
            names |= {r.name for r in regs} | {lp.name for lp in links}
    _regs, links = B.discover(B.LINK_ROOT, "link", {})
    return names | {lp.name for lp in links}


def _strings(path: Path) -> list[tuple[int, str]]:
    """Every string constant in a module's code, its docstrings aside."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs]


@pytest.mark.parametrize("module", ["freshness.py", "build.py"])
def test_the_verdict_and_the_build_name_no_registered_reader(module):
    """`freshness.py` and `build.py` name no reader (the note's guard 3): parsers come from
    the readers' declarations and the deployment set from `DEPLOYMENT_READER`, so a reader
    named here would be a rule only it obeys."""
    names = _registered_names()
    assert {"compose-services", "orderflow-write-mappings", "join-by-name"} <= names
    found = [(line, name) for line, text in _strings(ROOT / "tools" / "graph" / module)
             for name in names if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text)]
    assert found == []


def test_the_name_guard_sees_a_reader_named_in_code(tmp_path):
    """Its positive control: a module naming one in code is caught; in a docstring is not."""
    sample = tmp_path / "sample.py"
    sample.write_text('"""compose-services, in prose."""\nREADER = "compose-services"\n',
                      encoding="utf-8")
    assert _strings(sample) == [(2, "compose-services")]
