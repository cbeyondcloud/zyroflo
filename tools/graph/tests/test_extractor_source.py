"""The source-code extractors: endpoints, publications, subscriptions, and tests.

Three hops of the fixture's chain are answered here, and one decoy whose whole content is
something that must NOT be emitted. The section at the end checks all four against the
committed answer key, by line.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import _java, kafka, properties as P, spring  # noqa: E402
from tools.graph.extractors import tests as testsx  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"

pytestmark = pytest.mark.skipif(not _java.available(),
                                reason="no parser installed for this language")


# ── helpers ───────────────────────────────────────────────────────────────────


def make_view(pairs: dict[str, str], file: str = "src/main/resources/application.properties"):
    """A resolved configuration view, built the way the resolver builds one."""
    decls = [{"key": k, "relaxed": ids.relaxed_key(k), "raw": v, "file": file, "line": i + 1}
             for i, (k, v) in enumerate(pairs.items())]
    return P.resolve(decls)


def ctx_for(tmp_path: Path, files: dict[str, str], view: dict | None = None,
            alias: str = "svc") -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    cache = {P.CACHE_KEY: {alias: view}} if view is not None else {}
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          cache=cache, files={alias: paths})


def edges(res, type_: str):
    return [e for e in res.edges if e.type == type_]


CONTROLLER = """
package a.b;
@RestController
@RequestMapping("/v1")
public class C {
    @GetMapping("/list")
    public String list() { return "[]"; }
}
"""

PUBLISHER = """
package a.b;
public class P {
    @Value("${orders.publish.topic}")
    private String topic;
    private final KafkaTemplate<String, String> template;
    public void publish(String p) { template.send(topic, p); }
}
"""

LISTENER = """
package a.b;
public class L {
    @KafkaListener(topics = "${orders.consume.topic}", groupId = "g")
    public void on(String p) { }
}
"""


# ── the shared source-fact layer ──────────────────────────────────────────────


def test_a_class_carries_its_annotations_methods_and_fields():
    jf = _java.parse(PUBLISHER)
    cls = jf.classes[0]
    assert cls.name == "P"
    assert {f.name for f in cls.fields} == {"topic", "template"}
    assert cls.fields[0].annotation("Value").value == "${orders.publish.topic}"


def test_a_field_annotation_reports_the_declarations_line_not_the_declarators():
    """A reader asked where a value is bound must land on the annotation, which is where the
    binding is written — not on the line the variable name happens to sit."""
    jf = _java.parse("class P {\n    @Value(\"${k}\")\n    private String t;\n}\n")
    assert jf.classes[0].fields[0].annotation("Value").line == 2


def test_a_nested_class_is_its_own_class():
    """Folding an inner class into its parent attributes its mappings to a type that does not
    declare them, which is a wrong owner rather than a missing one."""
    jf = _java.parse("class Outer { static class Inner { @GetMapping(\"/x\") void m() {} } }")
    assert sorted(c.name for c in jf.classes) == ["Inner", "Outer"]
    inner = next(c for c in jf.classes if c.name == "Inner")
    assert inner.methods[0].annotation("GetMapping").value == "/x"


def test_a_fully_qualified_annotation_is_matched_on_its_last_segment():
    jf = _java.parse("class C { @org.example.Value(\"${k}\") String f; }")
    assert jf.classes[0].fields[0].annotation("Value") is not None


def test_named_and_unnamed_annotation_arguments_are_kept_apart():
    jf = _java.parse(LISTENER)
    ann = jf.classes[0].methods[0].annotation("KafkaListener")
    assert ann.value is None
    assert ann.args == {"topics": "${orders.consume.topic}", "groupId": "g"}


def test_one_declaration_of_several_variables_yields_several_fields():
    jf = _java.parse("class C { private String a, b; }")
    assert sorted(f.name for f in jf.classes[0].fields) == ["a", "b"]


def test_an_invocation_records_its_receiver_method_and_arguments():
    call = next(i for i in _java.parse(PUBLISHER).invocations if i.method == "send")
    assert (call.receiver, call.args) == ("template", ("topic", "p"))


def test_a_multiline_annotation_is_structure_not_text():
    """The reason this layer parses instead of matching: a mapping split across lines is
    ordinary formatting, and a line-oriented pattern simply misses it."""
    jf = _java.parse('class C {\n  @GetMapping(\n     "/list"\n  )\n  void m() {}\n}\n')
    assert jf.classes[0].methods[0].annotation("GetMapping").value == "/list"


# ── endpoints: the path is assembled, not read ────────────────────────────────


def test_an_endpoint_concatenates_context_path_class_and_method(tmp_path: Path):
    view = make_view({"server.servlet.context-path": "/orders"})
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": CONTROLLER}, view)
    res = spring.ControllersExtractor().extract(ctx)
    assert [e.dst for e in edges(res, "DG_EXPOSES")] == ["ep:/orders/v1/list"]


def test_the_class_mapping_line_is_recorded_beside_the_method_line(tmp_path: Path):
    view = make_view({"server.servlet.context-path": "/orders"})
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": CONTROLLER}, view)
    e = edges(spring.ControllersExtractor().extract(ctx), "DG_EXPOSES")[0]
    assert e.line == 6                       # the method mapping
    assert e.props["class_mapping_line"] == 4
    assert e.props["context_path_source"].endswith("application.properties:1")


def test_a_path_variable_is_erased_from_the_endpoint_identity(tmp_path: Path):
    """Two teams' taste in variable names is not two endpoints, and splitting them splits
    every impact query that touches either."""
    src = 'class C { @GetMapping("/detail/{orderId}") void d() {} }'
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": src}, make_view({}))
    assert edges(spring.ControllersExtractor().extract(ctx), "DG_EXPOSES")[0].dst \
        == "ep:/detail/{}"


def test_a_missing_context_path_lowers_confidence_and_is_reported(tmp_path: Path):
    """The endpoint recorded is a real fragment and a wrong identity: it will never match the
    caller's URL, so the graph shows endpoints nobody calls beside calls nobody serves."""
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": CONTROLLER}, make_view({}))
    res = spring.ControllersExtractor().extract(ctx)
    assert edges(res, "DG_EXPOSES")[0].confidence == 0.7
    assert any(r["kind"] == "missing-context-path" for r in res.unresolved)


def test_the_http_verb_comes_from_the_annotation(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": CONTROLLER}, make_view({}))
    assert edges(spring.ControllersExtractor().extract(ctx), "DG_EXPOSES")[0] \
        .props["http_method"] == "GET"


def test_a_controller_marker_is_recorded_but_not_required(tmp_path: Path):
    """Markers are routinely inherited from a base class; demanding one drops real endpoints
    without a word."""
    src = 'class C { @GetMapping("/x") void m() {} }'
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": src}, make_view({}))
    e = edges(spring.ControllersExtractor().extract(ctx), "DG_EXPOSES")[0]
    assert e.props["marked_controller"] is False


def test_a_class_with_no_mappings_exposes_nothing(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/main/java/E.java": "public enum E { A, B }"}, make_view({}))
    assert spring.ControllersExtractor().extract(ctx).edges == []


def test_a_mapping_in_a_test_source_is_not_a_served_endpoint(tmp_path: Path):
    """A mapping in a test is a fixture. Serving it invents an entity nothing deploys."""
    ctx = ctx_for(tmp_path, {"src/test/java/CTest.java": CONTROLLER}, make_view({}))
    assert spring.ControllersExtractor().extract(ctx).edges == []


# ── outbound calls declared as a pair of keys ─────────────────────────────────


def test_a_host_key_and_a_path_key_sharing_a_prefix_become_a_call(tmp_path: Path):
    view = make_view({"inventory.service.host": "http://inventory-api:8080",
                      "inventory.service.reserve.path": "/inventory/v1/reserve"})
    ctx = ctx_for(tmp_path, {"a.properties": ""}, view)
    e = edges(spring.RestCallsExtractor().extract(ctx), "DG_CALLS")[0]
    assert e.dst == "ep:/inventory/v1/reserve"
    assert e.line == 2 and e.props["host_line"] == 1
    assert e.props["host"] == "http://inventory-api:8080"


def test_a_host_with_no_path_is_reported_rather_than_dropped(tmp_path: Path):
    view = make_view({"inventory.service.host": "http://inventory-api:8080"})
    ctx = ctx_for(tmp_path, {"a.properties": ""}, view)
    res = spring.RestCallsExtractor().extract(ctx)
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["unpaired-host"]


def test_a_path_with_no_host_is_not_reported(tmp_path: Path):
    """Context paths and filesystem paths dominate this shape. Reporting every one of them
    would bury the findings that matter until someone stops reading the report."""
    view = make_view({"server.servlet.context-path": "/orders",
                      "reports.output.path": "/var/lib/reports"})
    ctx = ctx_for(tmp_path, {"a.properties": ""}, view)
    res = spring.RestCallsExtractor().extract(ctx)
    assert res.edges == [] and res.unresolved == []


def test_several_paths_on_one_host_are_several_calls(tmp_path: Path):
    view = make_view({"inv.host": "h", "inv.a.path": "/x/a", "inv.b.path": "/x/b"})
    ctx = ctx_for(tmp_path, {"a.properties": ""}, view)
    assert sorted(e.dst for e in edges(spring.RestCallsExtractor().extract(ctx), "DG_CALLS")) \
        == ["ep:/x/a", "ep:/x/b"]


def test_no_published_view_is_reported_not_assumed_empty(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"a.properties": ""})
    res = spring.RestCallsExtractor().extract(ctx)
    assert [r["kind"] for r in res.unresolved] == ["no-config-view"]


# ── subscriptions ─────────────────────────────────────────────────────────────


def test_a_listener_resolves_its_topic_through_the_configuration(tmp_path: Path):
    view = make_view({"orders.consume.topic": "order-events-v1"})
    ctx = ctx_for(tmp_path, {"src/main/java/L.java": LISTENER}, view)
    e = edges(kafka.ConsumersExtractor().extract(ctx), "DG_CONSUMES")[0]
    assert e.dst == "topic:order-events-v1"
    assert e.line == 4 and e.props["property"] == "orders.consume.topic"


@pytest.mark.parametrize("raw, expected", [
    ("${orders.consume.topic}", ["${orders.consume.topic}"]),
    ('{"a", "b"}', ["a", "b"]),
    ('{"a"}', ["a"]),
    ("plain-topic", ["plain-topic"]),
    ("", []),
])
def test_a_topic_argument_splits_on_its_own_shape(raw, expected):
    """The regression that the fixture caught.

    An array is `{"a","b"}` and a placeholder is `${a.b}` — BOTH contain braces. Stripping
    braces to handle the array turns the placeholder into `${a.b`, which no longer matches
    the placeholder pattern, so it is taken for a literal and becomes a topic named after the
    configuration key. Confidently, plausibly wrong rather than visibly broken.
    """
    assert kafka.topic_list(raw) == expected


def test_a_topic_that_cannot_be_resolved_produces_no_edge(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/main/java/L.java": LISTENER}, make_view({}))
    res = kafka.ConsumersExtractor().extract(ctx)
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["unresolved-topic"]


def test_a_subscription_to_a_key_configured_to_nothing_is_a_row_not_a_node(tmp_path: Path):
    """T535: a key that resolves to the empty string made a node named `topic:`, and every
    empty-valued key in the estate merged onto it - asserting links between components that
    share nothing. `kafka_python` refuses this case, and this module declared the
    `empty-topic-value` kind and never emitted it."""
    ctx = ctx_for(tmp_path, {"src/main/java/L.java": LISTENER},
                  make_view({"orders.consume.topic": ""}))
    res = kafka.ConsumersExtractor().extract(ctx)
    assert res.edges == [] and not [n for n in res.nodes if n.type == "topic"]
    found = [r for r in res.unresolved if r["kind"] == "empty-topic-value"]
    assert len(found) == 1 and found[0]["key"] == "orders.consume.topic", res.unresolved
    assert found[0]["raw"] == "${orders.consume.topic}"


def test_an_inline_empty_default_is_the_same_hole(tmp_path: Path):
    """The review's second shape: `${key:}` with no declaration resolves to its empty
    default, which needs no configuration file at all to reach `topic:`."""
    src = LISTENER.replace("${orders.consume.topic}", "${billing.audit.topic:}")
    res = kafka.ConsumersExtractor().extract(ctx_for(tmp_path, {"src/main/java/L.java": src},
                                                     make_view({})))
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["empty-topic-value"]


def test_a_publication_to_a_key_configured_to_nothing_is_a_row_not_a_node(tmp_path: Path):
    """T535's other Java path: the send reached `topic:` the same way."""
    ctx = ctx_for(tmp_path, {"src/main/java/P.java": PUBLISHER},
                  make_view({"orders.publish.topic": ""}))
    res = kafka.ProducersExtractor().extract(ctx)
    assert res.edges == [] and not [n for n in res.nodes if n.type == "topic"]
    assert [r["kind"] for r in res.unresolved] == ["empty-topic-value"]


def test_a_listener_naming_no_topic_is_reported(tmp_path: Path):
    src = "class L { @KafkaListener(groupId = \"g\") void on(String p) {} }"
    ctx = ctx_for(tmp_path, {"src/main/java/L.java": src}, make_view({}))
    res = kafka.ConsumersExtractor().extract(ctx)
    assert [r["kind"] for r in res.unresolved] == ["listener-without-topic"]


def test_a_listener_in_a_test_source_is_skipped(tmp_path: Path):
    view = make_view({"orders.consume.topic": "t"})
    ctx = ctx_for(tmp_path, {"src/test/java/LTest.java": LISTENER}, view)
    assert kafka.ConsumersExtractor().extract(ctx).edges == []


# ── publications, reconstructed rather than declared ──────────────────────────


def test_a_send_on_a_client_resolves_the_field_it_names(tmp_path: Path):
    view = make_view({"orders.publish.topic": "order-events-v1"})
    ctx = ctx_for(tmp_path, {"src/main/java/P.java": PUBLISHER}, view)
    e = edges(kafka.ProducersExtractor().extract(ctx), "DG_PRODUCES")[0]
    assert e.dst == "topic:order-events-v1"
    assert e.props["property"] == "orders.publish.topic"


def test_a_publication_is_cited_where_the_topic_name_is_BOUND(tmp_path: Path):
    """Not where the send happens. The send only uses the name; the annotation is where the
    fact being asserted actually lives, and it is the line a reader needs."""
    view = make_view({"orders.publish.topic": "t"})
    ctx = ctx_for(tmp_path, {"src/main/java/P.java": PUBLISHER}, view)
    e = edges(kafka.ProducersExtractor().extract(ctx), "DG_PRODUCES")[0]
    assert e.line == 4                      # the @Value annotation
    assert e.props["send_line"] == 7        # the call, recorded but not cited


def test_a_literal_destination_is_taken_as_written(tmp_path: Path):
    src = ("class P { private KafkaTemplate<String,String> t;\n"
           "  void go() { t.send(\"audit-events\", \"x\"); } }")
    ctx = ctx_for(tmp_path, {"src/main/java/P.java": src}, make_view({}))
    assert edges(kafka.ProducersExtractor().extract(ctx), "DG_PRODUCES")[0].dst \
        == "topic:audit-events"


def test_a_destination_that_is_an_expression_is_reported_never_guessed(tmp_path: Path):
    """Guessing from a variable's name is how a topic that does not exist enters the graph,
    and nothing downstream could tell it from a real one."""
    src = ("class P { private KafkaTemplate<String,String> t;\n"
           "  void go(String s) { t.send(prefix + s, \"x\"); } }")
    ctx = ctx_for(tmp_path, {"src/main/java/P.java": src}, make_view({}))
    res = kafka.ProducersExtractor().extract(ctx)
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["unresolved-topic"]


def test_a_send_on_something_that_is_not_a_client_is_ignored(tmp_path: Path):
    src = ("class P { private MailSender t;\n"
           "  void go() { t.send(\"hello\", \"x\"); } }")
    ctx = ctx_for(tmp_path, {"src/main/java/P.java": src}, make_view({}))
    assert kafka.ProducersExtractor().extract(ctx).edges == []


def test_a_class_with_no_client_field_publishes_nothing(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/main/java/C.java": CONTROLLER}, make_view({}))
    assert kafka.ProducersExtractor().extract(ctx).edges == []


# ── D5: a test that names an endpoint is not a caller ─────────────────────────


TEST_SRC = """
package a.b;
/** exercises /orders/v1/list, mentioned here in prose. */
public class CTest {
    @Test
    public void t() {
        String mockPath = "/orders/v1/list";
    }
}
"""


def test_a_test_suite_covers_its_component(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/test/java/CTest.java": TEST_SRC})
    res = testsx.TestsExtractor().extract(ctx)
    to_svc = [e for e in res.edges if e.dst.startswith("svc:")]
    assert len(to_svc) == 1 and to_svc[0].type == "DG_TESTS"


def test_a_path_named_in_a_test_never_becomes_a_call(tmp_path: Path):
    """The decoy's whole content. Once a phantom caller exists it appears in every blast
    radius for that endpoint forever, arriving as a caller nobody can find — which is worse
    than a missing edge, because someone will go looking for it."""
    ctx = ctx_for(tmp_path, {"src/test/java/CTest.java": TEST_SRC})
    res = testsx.TestsExtractor().extract(ctx)
    assert not [e for e in res.edges if e.type == "DG_CALLS"]
    to_ep = [e for e in res.edges if e.dst.startswith("ep:")]
    assert len(to_ep) == 1 and to_ep[0].type == "DG_TESTS"


def test_a_path_in_a_comment_is_not_a_mention(tmp_path: Path):
    """Read as text the source above names the path twice. Read as a parse tree it is one
    literal and one comment, and only the literal is a claim about behaviour."""
    ctx = ctx_for(tmp_path, {"src/test/java/CTest.java": TEST_SRC})
    res = testsx.TestsExtractor().extract(ctx)
    to_ep = [e for e in res.edges if e.dst.startswith("ep:")]
    assert [e.line for e in to_ep] == [7]


def test_a_mention_carries_reduced_confidence(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/test/java/CTest.java": TEST_SRC})
    res = testsx.TestsExtractor().extract(ctx)
    assert [e.confidence for e in res.edges if e.dst.startswith("ep:")] == [0.8]


@pytest.mark.parametrize("literal, expected", [
    ("/orders/v1/list", True),
    ("/a/b", True),
    ("/", False),
    ("/single", False),
    ("[]", False),
    ("/var/lib/report.json", False),
    ("/path with space/x", False),
])
def test_what_counts_as_a_path_literal(literal, expected):
    assert ids.looks_like_path(literal) is expected


def test_a_resource_under_a_test_tree_is_not_a_suite(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/test/resources/data.json": "{}"})
    assert testsx.TestsExtractor().extract(ctx).nodes == []


# ── registration ──────────────────────────────────────────────────────────────


def test_every_source_extractor_registers_and_declares_its_dependency():
    regs = {r.name: r for r in B.discover_extractors()}
    for name in ("spring-controllers", "spring-call-concats",
                 "kafka-producers", "kafka-consumers"):
        assert regs[name].requires == ("properties",), name
    # Item 92: the properties pass must not re-report a host the code pairs, so it
    # runs after the concat pass and reads its paired-key set from the cache.
    assert regs["spring-rest-calls"].requires == ("properties", "spring-call-concats")
    assert regs["tests"].requires == ()


def test_the_shared_source_layer_is_not_registered_as_an_extractor():
    """Its directory starts with `_`, which the registry skips — that is what lets several
    extractors share one parse without the shared code pretending to be one of them."""
    assert "_java" not in {r.module.rsplit("/", 1)[-1] for r in B.discover_extractors()}


# ── against the committed fixture ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def fixture_edges(tmp_path_factory):
    """Every edge of a real build of the committed fixture, through the whole pipeline.

    Through the pipeline rather than by calling extractors directly, because the ordering
    the shared cache depends on is part of what produces these edges — and testing the parts
    in isolation is exactly how that dependency stayed invisible until a build ran.
    """
    import json

    out = tmp_path_factory.mktemp("graph")
    B.build(FIXTURE / "workspace.toml", out, run_id="fixture01")
    return [json.loads(line)
            for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
            if '"kind": "edge"' in line]


def one(fixture_edges, type_, dst):
    hits = [e for e in fixture_edges if e["type"] == type_ and e["dst"] == dst]
    assert len(hits) == 1, f"expected exactly one {type_} -> {dst}, got {len(hits)}"
    return hits[0]


def test_fixture_hop2_the_endpoint_is_context_path_plus_class_plus_method(fixture_edges):
    e = one(fixture_edges, "DG_EXPOSES", "ep:/orders/v1/list")
    assert e["src"] == ids.component_service_id("orders-api")
    assert e["source_file"].endswith("OrderController.java")
    assert e["line"] == 12, "EXPECTED.md hop 2 cites OrderController.java:12"
    assert e["props"]["class_mapping_line"] == 9, "and :9 for the class mapping"
    assert e["props"]["context_path"] == "/orders"


def test_fixture_hop3_the_publication_is_cited_at_the_value_binding(fixture_edges):
    e = one(fixture_edges, "DG_PRODUCES", "topic:order-events-v1")
    assert e["src"] == ids.component_service_id("orders-api")
    assert e["source_file"].endswith("OrderPublisher.java")
    assert e["line"] == 9, "EXPECTED.md hop 3 cites OrderPublisher.java:9"
    assert e["props"]["resolved_from"].endswith("application.properties:5")


def test_fixture_hop4_the_subscription_reaches_the_same_topic_by_another_key(fixture_edges):
    e = one(fixture_edges, "DG_CONSUMES", "topic:order-events-v1")
    assert e["src"] == ids.component_service_id("orders-worker")
    assert e["line"] == 8, "EXPECTED.md hop 4 cites OrderConsumer.java:8"
    assert e["props"]["property"] == "orders.consume.topic"


def test_fixture_the_producer_and_consumer_meet_at_one_topic_node(fixture_edges):
    """The pairing is not computed — it falls out of both edges naming the same RESOLVED
    value. An extractor pairing on key names would find nothing, since the two keys differ."""
    produced = one(fixture_edges, "DG_PRODUCES", "topic:order-events-v1")
    consumed = one(fixture_edges, "DG_CONSUMES", "topic:order-events-v1")
    assert produced["props"]["property"] != consumed["props"]["property"]


def test_fixture_the_second_endpoint_normalises_its_path_variable(fixture_edges):
    e = one(fixture_edges, "DG_EXPOSES", "ep:/orders/v1/detail/{}")
    assert e["line"] == 17, "EXPECTED.md cites OrderController.java:17"


def test_fixture_the_outbound_call_pairs_a_host_with_a_path(fixture_edges):
    e = one(fixture_edges, "DG_CALLS", "ep:/inventory/v1/reserve")
    assert e["source_file"].endswith("application.properties")
    assert (e["line"], e["props"]["host_line"]) == (17, 16), \
        "EXPECTED.md cites application.properties:16-17"


def test_fixture_d5_the_test_produces_no_caller(fixture_edges):
    assert not [e for e in fixture_edges
                if e["type"] == "DG_CALLS" and "Test" in e["source_file"]]


def test_fixture_d5_the_test_names_the_endpoint_exactly_once(fixture_edges):
    """Twice in the file, once as a literal. A text scan would produce two."""
    hits = [e for e in fixture_edges
            if e["type"] == "DG_TESTS" and e["dst"] == "ep:/orders/v1/list"]
    assert len(hits) == 1 and hits[0]["line"] == 15


def test_fixture_the_inert_component_produces_no_edge_to_anything_else(fixture_edges):
    """P5, at this layer: every edge legacy-reports takes part in stays inside it."""
    inert = ids.component_service_id("legacy-reports")
    outward = [e for e in fixture_edges
               if (e["src"] == inert or e["dst"] == inert)
               and not (e["src"].endswith("legacy-reports")
                        or e["dst"].endswith("legacy-reports")
                        or "legacy-reports" in e["src"] or "legacy-reports" in e["dst"])]
    assert outward == []
