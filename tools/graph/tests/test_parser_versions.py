"""Batch 22 (T631; design pass 22, section 2): a shard's parser stamp records each parser's
VERSION - the version of what served it - or `false` where it is missing, and claims 8 and 10
compare versions. A version that cannot be read is `"installed"`, compared by presence alone,
so an environment that cannot read one never stales another's shard on it; a legacy `true`
records no version and is stale; a legacy `false` compares as any `false`.

Synthetic readings over a real activation, as `test_freshness_judge.py` builds them: which
readers read which files, and which parser each file needs, come from the real registry.
"""
from __future__ import annotations

import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness as F, snapshot as SN  # noqa: E402
from tools.graph.core.lib import srcfacts  # noqa: E402
from tools.graph.core.model import ExtractResult  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, producer, spawn, svc  # noqa: E402

#: `a`'s compose file is read through `compose-services` (YAML, `a`'s own half); `b`'s producer
#: reads it through the shared cache, so `b`'s closure half reaches YAML.
FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
NOW = "PyYAML==6.0.2"
OLD = "PyYAML==6.0.1"
#: A Java file, which `kafka` and `spring` read through the Java grammar.
JAVA = "class Send {}\n"


def ticked(readers) -> str:
    return ", ".join(f"`{r}`" for r in readers)


class Feed:
    """FEED laid down and judged with the real registry; `versions` is what each parser's
    version reads now (`"installed"` where unset), `missing` the parsers not installed now."""

    def __init__(self, where: Path):
        lay_down(where, FEED)
        self.root = where / "ws"
        self.components = [{"name": c, "path": c} for c in FEED]
        self.regs = B.discover_extractors()
        self.act = B.activate(self.regs, self.components, self.root, workspace=self.components)
        self.closure = B.cross_closure(self.regs)   # what the build hands the verdict
        self.parser_of = B.parser_rule(self.regs)
        self.digests = B.input_digests(self.act, self.components, self.root)
        self.version = B.extractor_version(self.regs)
        self.config = {c["name"]: "shared:own" for c in self.components}
        self.versions: dict[str, str] = {"yaml": NOW}
        self.missing: set[str] = set()

    def probe(self, parser: str) -> bool:
        return parser not in self.missing

    def parser_version(self, parser: str) -> str:
        return self.versions.get(parser, "installed")

    def halves(self, name: str):
        return F.parser_halves(name, self.act, self.closure, self.parser_of, self.root,
                               self.components)

    def stamp(self, name: str) -> dict:
        return F.parser_stamp(name, self.act, self.closure, self.probe, self.parser_of,
                              self.root, self.components, version=self.parser_version)

    def current(self, name: str, **parsers) -> SN.ShardReading:
        stamp = {**self.stamp(name), **parsers}
        return SN.ShardReading(stamps={"inputs": dict(self.digests.get(name, {})),
                                       "extractor_version": self.version,
                                       "config_sha": self.config[name], "sha256": "0" * 64,
                                       "parsers": stamp,
                                       "closure_inputs": F.closure_record(
                                           name, self.act, self.closure, self.digests)},
                                facts=ExtractResult())

    def judge(self, **readings: SN.ShardReading) -> dict[str, F.Verdict]:
        full = {c["name"]: readings.get(c["name"]) or self.current(c["name"])
                for c in self.components}
        return F.judge(full, self.components, self.act, self.closure, self.digests,
                       self.version, self.config, self.probe, self.parser_of, self.root,
                       parser_version=self.parser_version)


@pytest.fixture
def feed(tmp_path) -> Feed:
    return Feed(tmp_path)


def stale(words: str) -> F.Verdict:
    return F.Verdict("stale", words)


def test_the_stamp_records_each_parsers_version_and_false_where_missing(feed):
    own, near = feed.halves("b")
    assert "yaml" in near, "b's closure half reaches YAML through a's compose file"
    feed.missing = set(own) - {"yaml"}
    expected = {p: (False if p in feed.missing else feed.parser_version(p))
                for p in sorted({*own, *near})}
    assert feed.stamp("b") == expected
    assert feed.stamp("b")["yaml"] == NOW


def test_claim_8_says_which_version_its_readers_read_with(feed):
    own, _near = feed.halves("a")
    assert feed.judge(a=feed.current("a", yaml=OLD))["a"] == stale(
        f"{ticked(own['yaml'])} read with `yaml` {OLD}; it is {NOW} now")


def test_claim_10_says_which_version_a_dependency_read_with(feed):
    own, near = feed.halves("b")
    assert "yaml" not in own
    assert feed.judge(b=feed.current("b", yaml=OLD))["b"] == stale(
        f"depends on {', '.join(near['yaml'])}, whose parser `yaml` was {OLD} and is {NOW} now")


def test_a_version_read_as_installed_on_either_side_compares_presence_alone(feed):
    assert feed.judge(a=feed.current("a", yaml="installed"))["a"] == F.Verdict("current")
    feed.versions["yaml"] = "installed"
    assert feed.judge(a=feed.current("a", yaml=OLD))["a"] == F.Verdict("current")


def test_a_legacy_true_records_no_version_and_is_stale(feed):
    assert feed.judge(a=feed.current("a", yaml=True))["a"] == stale(
        "its shard's parser stamp records no version for `yaml`")
    feed.missing = {"yaml"}
    own, _near = feed.halves("a")
    assert feed.judge(a=feed.current("a", yaml=True))["a"] == stale(
        f"its shard was published while {ticked(own['yaml'])} had a parser that is no longer "
        f"installed"), "missing now: claim 8's removal is the truer sentence (D22-49)"


def test_a_legacy_true_in_the_closure_half_records_no_version_too(feed):
    """D22-35: the words are one rendering in either claim - `b` reaches YAML only through the
    readers it depends on (claim 10)."""
    own, near = feed.halves("b")
    assert "yaml" in near and "yaml" not in own
    assert feed.judge(b=feed.current("b", yaml=True))["b"] == stale(
        "its shard's parser stamp records no version for `yaml`")


def test_a_legacy_false_compares_as_any_false(feed):
    feed.missing = {"yaml"}
    assert feed.judge(a=feed.current("a", yaml=False))["a"] == F.Verdict("current")
    feed.missing = set()
    own, _near = feed.halves("a")
    assert feed.judge(a=feed.current("a", yaml=False))["a"] == stale(
        f"its shard was published while {ticked(own['yaml'])} had no parser installed")


@pytest.mark.parametrize("parsers, fault", [
    ({"yaml": NOW}, None),
    ({"yaml": True}, None),
    ({"yaml": False}, None),
    ({"yaml": ""}, "`parsers` is not a mapping of versions"),
    ({"yaml": 6}, "`parsers` is not a mapping of versions"),
    ({"yaml": None}, "`parsers` is not a mapping of versions"),
])
def test_the_stamp_block_types_a_version_or_a_legacy_boolean(parsers, fault):
    assert SN._stamp_block_fault({"sha256": "0" * 64, "parsers": parsers}) == fault


def test_a_new_manifest_is_schema_version_3(tmp_path):
    SN.write_shard(tmp_path, ExtractResult(), {"inputs": {}, "parsers": {"yaml": NOW}})
    assert json.loads((tmp_path / SN.MANIFEST).read_text(encoding="utf-8"))["schema_version"] == 3


def test_a_schema_1_or_2_manifest_still_loads(tmp_path):
    """The number gates no reading (D22-34): the reader keys on the keys a manifest holds."""
    for schema, extra in ((1, {}), (2, {"inputs": {}, "extractor_version": "x",
                                        "config_sha": "y", "parsers": {"yaml": True}})):
        dir_ = tmp_path / str(schema)
        dir_.mkdir()
        SN.write_shard(dir_, ExtractResult(), None)
        recorded = json.loads((dir_ / SN.MANIFEST).read_text(encoding="utf-8"))
        recorded.update({"schema_version": schema, **extra})
        (dir_ / SN.MANIFEST).write_text(json.dumps(recorded), encoding="utf-8")
        assert SN.read_manifest(dir_).state == "ok"


def _dist_version(module: str) -> str:
    dists = sorted(set(metadata.packages_distributions().get(module.split(".")[0], [])))
    return "+".join(f"{d}=={metadata.version(d)}" for d in dists)


def test_the_versioner_reads_yaml_and_the_route_that_served_a_grammar():
    version = B.parser_versioner(B.discover_extractors())
    assert version("yaml") == _dist_version("yaml"), "YAML's version alone, no runtime suffix"
    assert "tree-sitter" not in version("yaml")
    served = [lang for lang in srcfacts._MODULES if srcfacts.available(lang)]
    if served:   # branch and assert, never skip
        lang = served[0]
        route = srcfacts.served_by(lang)
        assert version(lang) == f"{_dist_version(route)}+{_dist_version('tree_sitter')}"
    else:
        assert all(version(lang) == "installed" for lang in srcfacts._MODULES)


def test_the_versioner_establishes_the_route_itself(monkeypatch):
    """D22-32: the route record is empty until a language is asked for, so the versioner asks
    `available` before `served_by` - a process that has parsed nothing still reads the
    version of what serves the grammar, never `"installed"` where a route exists."""
    monkeypatch.setattr(srcfacts, "_SERVED", {})
    srcfacts._parser.cache_clear()
    version = B.parser_versioner(B.discover_extractors())
    for lang in srcfacts._MODULES:
        srcfacts._parser.cache_clear()
        monkeypatch.setattr(srcfacts, "_SERVED", {})
        now = version(lang)
        if srcfacts.available(lang):   # branch and assert, never skip
            route = srcfacts.served_by(lang)
            assert now == f"{_dist_version(route)}+{_dist_version('tree_sitter')}", lang
        else:
            assert now == "installed", lang


def test_an_absent_distribution_reads_installed_never_a_traceback(monkeypatch):
    monkeypatch.setattr(B, "_distributions", lambda: {})
    version = B.parser_versioner(B.discover_extractors())
    assert version("yaml") == "installed"
    assert all(version(lang) == "installed" for lang in srcfacts._MODULES)


PROFILE = '''\
from tools.graph.core.model import ExtractResult


class TomlReader:
    name = "b22-toml"
    INPUT_GLOBS = ("*.toml",)
    PARSERS = ("toml",)

    def parser_available(self, name):
        return True

    def parser_of(self, path, rel, text=None):
        return "toml"

    def parser_version(self, name):
        {answer}

    def extract(self, ctx):
        return ExtractResult()


EXTRACTORS = (TomlReader(),)
'''


@pytest.mark.parametrize("answer", ['raise ValueError("boom")', 'return ""', "return 3"])
def test_a_readers_parser_version_that_raises_or_is_no_string_is_refused(tmp_path, answer):
    toml = lay_down(tmp_path, {"a": {"conf.toml": "x = 1\n"}})
    toml.write_text(toml.read_text(encoding="utf-8").replace('profile = ""', 'profile = "./prof"'),
                    encoding="utf-8")
    (toml.parent / "prof").mkdir()
    (toml.parent / "prof" / "__init__.py").write_text(PROFILE.replace("{answer}", answer),
                                                      encoding="utf-8")
    proc = spawn(tmp_path, "--publish-snapshot")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "`b22-toml`'s `parser_version('toml')` returned" in proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_parser_version_that_is_not_a_callable_is_refused_at_registration(tmp_path):
    """Validated where the reader registers, as `claims_nothing` is - never a traceback when
    the stamp first asks it."""
    toml = lay_down(tmp_path, {"a": {"conf.toml": "x = 1\n"}})
    toml.write_text(toml.read_text(encoding="utf-8").replace('profile = ""', 'profile = "./prof"'),
                    encoding="utf-8")
    (toml.parent / "prof").mkdir()
    body = PROFILE.replace("    def parser_version(self, name):\n        {answer}\n",
                           '    parser_version = "1.0"\n')
    assert "parser_version = " in body
    (toml.parent / "prof" / "__init__.py").write_text(body, encoding="utf-8")
    proc = spawn(tmp_path, "--publish-snapshot")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert ("`parser_version` on 'b22-toml' is '1.0'; it must be a callable `(parser name) -> "
            "version`, or absent") in proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_fresh_check_fresh_reads_the_versions_the_publish_wrote(tmp_path):
    """D22-32: a door that parses nothing (`--check-fresh`) stamps the text the publish did,
    since the version asks `available` first, which establishes the route."""
    lay_down(tmp_path, FEED)
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    stamped = json.loads((tmp_path / "ws" / "a" / SN.SHARD_DIR / SN.MANIFEST)
                         .read_text(encoding="utf-8"))["parsers"]
    assert stamped["yaml"] == _dist_version("yaml"), stamped
    proc = spawn(tmp_path, "--check-fresh", out="check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_fresh_check_fresh_sees_a_grammar_version_that_moved(tmp_path):
    """A grammar's version recorded at the publish, then another one stamped in its place (a
    machine that published with another release): a fresh `--check-fresh`, which parses
    nothing, names the version that moved (claim 8)."""
    lay_down(tmp_path, {**FEED, "j": {"Send.java": JAVA}})
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    manifest = tmp_path / "ws" / "j" / SN.SHARD_DIR / SN.MANIFEST
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    if not srcfacts.available("java"):   # branch and assert, never skip
        assert recorded["parsers"]["java"] is False, recorded["parsers"]
        return
    now = f"{_dist_version(srcfacts.served_by('java'))}+{_dist_version('tree_sitter')}"
    assert recorded["parsers"]["java"] == now, recorded["parsers"]
    old = "tree-sitter-java==0.0.1+tree-sitter==0.0.1"
    recorded["parsers"]["java"] = old
    manifest.write_text(json.dumps(recorded), encoding="utf-8")
    proc = spawn(tmp_path, "--check-fresh", out="check")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.startswith("freshness: STALE ")]
    assert len(lines) == 1 and lines[0].startswith("freshness: STALE j - "), proc.stdout
    assert f"read with `java` {old}; it is {now} now" in lines[0], proc.stdout
