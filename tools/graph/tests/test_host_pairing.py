"""Host pairing: values decide, and the code's own concatenations join what keys cannot.

Item 92, two measured mechanisms. First: key-suffix classification is value-blind — on a
real estate 505 of 1,539 "unpaired hosts" were PATH values under `.url`/`.endpoint`
keys, and 224 more were schemes that are no rest host at all — so `spring-rest-calls`
now classifies by VALUE (an address starts http(s)://, a path starts /), suffixes
remaining a hint only where a value cannot speak. Second: 636 + 904 call sites join a
host and a path by string concatenation through item 91's binding layer — prefixes
unrelated, the pair declared nowhere but in the argument — read by the new
`spring-call-concats` extractor, one rule per test below.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import properties as props_mod  # noqa: E402
from tools.graph.extractors import spring  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def ctx_for(tmp_path: Path, tree: dict[str, str], alias: str = "svc") -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: paths})


def run_pairs(tmp_path: Path, properties: str):
    ctx = ctx_for(tmp_path, {"src/main/resources/application.properties": properties})
    for ex in props_mod.EXTRACTORS:
        ex.extract(ctx)
    return spring.RestCallsExtractor().extract(ctx)


def run_concats(tmp_path: Path, tree: dict[str, str]):
    ctx = ctx_for(tmp_path, tree)
    for ex in props_mod.EXTRACTORS:
        ex.extract(ctx)
    return spring.CallConcatsExtractor().extract(ctx)


def calls(res):
    return [e for e in res.edges if e.type == "DG_CALLS"]


def unpaired(res):
    return [u for u in res.unresolved if u["kind"] == "unpaired-host"]


# -- values decide ------------------------------------------------------------------------


def test_a_url_key_holding_a_path_pairs_with_its_addressed_sibling(tmp_path):
    res = run_pairs(tmp_path, "billing.core.host=http://billing:8080\n"
                              "billing.core.search.url=/billing/v1/_search\n")
    (edge,) = calls(res)
    assert edge.dst == "ep:/billing/v1/_search"
    assert edge.props["host_key"] == "billing.core.host"
    assert not unpaired(res)


def test_a_path_valued_key_is_never_an_unpaired_host(tmp_path):
    res = run_pairs(tmp_path, "some.search.url=/things/_search\n")
    assert not calls(res) and not unpaired(res), \
        "a value starting / is a path whatever its key ends with"


def test_a_scheme_that_is_no_rest_host_is_excluded_entirely(tmp_path):
    res = run_pairs(tmp_path, "cache.store.url=redis://cache:6379\n")
    assert not calls(res) and not unpaired(res), \
        "a redis address is a different fact class, not a dangling rest host"


def test_a_bare_word_with_a_host_suffix_keeps_the_suffix_reading(tmp_path):
    """`kafka-broker:9092` has no scheme and no slash — the value cannot speak, so the
    suffix remains the hint and the unpaired report survives for it."""
    res = run_pairs(tmp_path, "queue.broker.host=broker-one:9092\n")
    (row,) = unpaired(res)
    assert row["key"] == "queue.broker.host"


def test_an_address_with_no_path_sibling_is_still_reported(tmp_path):
    res = run_pairs(tmp_path, "lonely.service.host=http://lonely:8080\n")
    (row,) = unpaired(res)
    assert row["key"] == "lonely.service.host" and row["value"].startswith("http")


# -- the code's concatenations ---------------------------------------------------------------

PROPS = ("reporting.host=http://reports:8080\n"
         "search.list.path=/reports/v1/list\n")

PATH_CFG = """\
@Getter
public class PathCfg {
    @Value("${search.list.path}")
    private String searchListPath;
}
"""

HOST_CFG = """\
@Getter
public class HostCfg {
    @Value("${reporting.host}")
    private String reportingHost;
}
"""


def test_a_same_class_field_plus_a_getter_is_a_call_pair(tmp_path):
    res = run_concats(tmp_path, {
        "src/main/resources/application.properties": PROPS,
        "src/main/java/x/PathCfg.java": PATH_CFG,
        "src/main/java/x/Client.java": """\
public class Client {
    @Value("${reporting.host}")
    private String reportingHost;
    private PathCfg pathCfg;
    private RestTemplate restTemplate;
    public String fetch() {
        return restTemplate.getForObject(reportingHost + pathCfg.getSearchListPath(), String.class);
    }
}
"""})
    (edge,) = calls(res)
    assert edge.dst == "ep:/reports/v1/list"
    assert edge.source_file.endswith("Client.java") and edge.line == 7
    assert edge.confidence == 0.9
    assert edge.props["via"] == "getter-concat"
    assert edge.props["host_key"] == "reporting.host"
    assert edge.props["path_key"] == "search.list.path"


def test_two_getters_across_two_config_classes_pair(tmp_path):
    res = run_concats(tmp_path, {
        "src/main/resources/application.properties": PROPS,
        "src/main/java/x/PathCfg.java": PATH_CFG,
        "src/main/java/x/HostCfg.java": HOST_CFG,
        "src/main/java/x/Client.java": """\
public class Client {
    private HostCfg hostCfg;
    private PathCfg pathCfg;
    private RestTemplate restTemplate;
    public String fetch() {
        return restTemplate.getForObject(hostCfg.getReportingHost() + pathCfg.getSearchListPath(), String.class);
    }
}
"""})
    (edge,) = calls(res)
    assert edge.dst == "ep:/reports/v1/list"


def test_an_append_chain_pairs_the_same_way(tmp_path):
    res = run_concats(tmp_path, {
        "src/main/resources/application.properties": PROPS,
        "src/main/java/x/PathCfg.java": PATH_CFG,
        "src/main/java/x/HostCfg.java": HOST_CFG,
        "src/main/java/x/Client.java": """\
public class Client {
    private HostCfg hostCfg;
    private PathCfg pathCfg;
    public String url() {
        return new StringBuilder().append(hostCfg.getReportingHost()).append(pathCfg.getSearchListPath()).toString();
    }
}
"""})
    (edge,) = calls(res)
    assert edge.dst == "ep:/reports/v1/list"
    assert edge.props["via"] == "getter-concat"


def test_an_address_whose_path_half_does_not_resolve_is_reported(tmp_path):
    res = run_concats(tmp_path, {
        "src/main/resources/application.properties": "reporting.host=http://reports:8080\n",
        "src/main/java/x/HostCfg.java": HOST_CFG,
        "src/main/java/x/Client.java": """\
public class Client {
    private HostCfg hostCfg;
    private RestTemplate restTemplate;
    public String fetch(String tail) {
        return restTemplate.getForObject(hostCfg.getReportingHost() + tail, String.class);
    }
}
"""})
    assert not calls(res)
    (row,) = unpaired(res)
    assert row["key"] == "reporting.host"
    assert row["source_file"].endswith("Client.java")


def test_a_concat_of_non_addresses_is_silence_not_noise(tmp_path):
    res = run_concats(tmp_path, {
        "src/main/resources/application.properties": PROPS,
        "src/main/java/x/Client.java": """\
public class Client {
    public String label(String a, String b) {
        return log.format(a + b, "x" + "y");
    }
}
"""})
    assert not calls(res) and not res.unresolved, \
        "most concatenations are not URLs; reporting them would bury the report"


# -- the fixture, held to EXPECTED.md ------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import json
    out = tmp_path_factory.mktemp("h92")
    B.build(FIXTURE / "workspace.toml", out, run_id="h92")
    rows = [json.loads(l) for l
            in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    edges = [r for r in rows if "src" in r]
    unresolved = [json.loads(l) for l in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines() if l]
    return edges, unresolved


def test_h1_the_pair_the_values_declare(built):
    edges, _ = built
    (edge,) = [e for e in edges if e["type"] == "DG_CALLS"
               and e["dst"] == "ep:/billing/v1/_search"]
    assert edge["src"] == "svc:orders-api@orders-api"
    assert edge["source_file"].endswith("application.properties") and edge["line"] == 27


def test_h2_the_pair_only_the_code_declares(built):
    edges, _ = built
    (edge,) = [e for e in edges if e["type"] == "DG_CALLS"
               and e["dst"] == "ep:/reports/v1/list"]
    assert edge["source_file"].endswith("ReportsClient.java") and edge["line"] == 20
    assert edge["confidence"] == 0.9 and edge["props"]["via"] == "getter-concat"


def test_the_fixture_unresolved_settles_back_at_five(built):
    _, unresolved = built
    assert len(unresolved) == 5
    assert not [u for u in unresolved if u["kind"] == "unpaired-host"], \
        "all four transitional rows were converted: two into H1, one into H2, and the " \
        "redis address out of this fact class entirely"
