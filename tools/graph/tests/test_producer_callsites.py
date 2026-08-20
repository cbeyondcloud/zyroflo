"""Producer call sites resolved through wrappers and config-class bindings (item 91).

The estate fact this answers: services publish through a component-local wrapper, and
topic names live in `@Value`-bound config classes reached through getters — so the send
call the old extractor read had a method parameter where the topic should be, and the
real name sat two declared links away. Measured before design: 18 PRODUCES vs 218
CONSUMES, 584 getter-argument wrapper calls vs 47 literals.

Every resolution rule here is a declared link or it does not resolve: a wrapper is a
method whose PARAMETER FORWARDS into a client send (never a method name); a getter maps
to a field it actually has, either as a written-out method or under a class-level
Lombok @Getter/@Data; the receiver's DECLARED FIELD TYPE picks the config class. One
level of forwarding is the stated bound — a caller passing its own parameter stays a
recorded row, because resolving it is whole-program dataflow and guessing is how a
topic that does not exist enters the graph.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import kafka  # noqa: E402
from tools.graph.extractors import properties as props_mod  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def ctx_for(tmp_path: Path, by_alias: dict[str, dict[str, str]]) -> ExtractContext:
    repos, files = [], {}
    for alias, tree in by_alias.items():
        root = tmp_path / alias
        paths = []
        for rel, body in tree.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            paths.append(p)
        repos.append(RepoRef(alias=alias, path=root))
        files[alias] = paths
    return ExtractContext(repos=repos, run_id="r", files=files)


def run(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    """Properties first (it publishes the resolved view), then the producer pass."""
    ctx = ctx_for(tmp_path, {alias: files})
    for ex in props_mod.EXTRACTORS:
        ex.extract(ctx)
    return kafka.ProducersExtractor().extract(ctx)


def produces(res):
    return [e for e in res.edges if e.type == "DG_PRODUCES"]


PROPS = "orders.notify.topic=order-notify-v1\norders.stream.topic=order-stream-v1\n"

WRAPPER = """\
public class Gateway {
    private KafkaTemplate<String, Object> kafkaTemplate;
    public void emit(String destination, Object payload) {
        kafkaTemplate.send(destination, payload);
    }
}
"""

CONFIG = """\
@Getter
public class Cfg {
    @Value("${orders.notify.topic}")
    private String notifyTopic;
    @Value("${orders.stream.topic}")
    private String streamTopic;
    public String getNotifyTopic() { return notifyTopic; }
}
"""


def tree(caller: str) -> dict[str, str]:
    return {"src/main/resources/application.properties": PROPS,
            "src/main/java/x/Gateway.java": WRAPPER,
            "src/main/java/x/Cfg.java": CONFIG,
            "src/main/java/x/Caller.java": caller}


# -- the wrapper --------------------------------------------------------------------------


def test_a_wrapper_call_with_a_literal_produces_at_the_call_site(tmp_path):
    res = run(tmp_path, tree("""\
public class Caller {
    private Gateway gateway;
    public void go(Object evt) {
        gateway.emit("audit-raw", evt);
    }
}
"""))
    (edge,) = produces(res)
    assert edge.dst == "topic:audit-raw"
    assert edge.source_file.endswith("Caller.java") and edge.line == 4
    assert edge.confidence == 1.0
    assert edge.props["via"] == "wrapper"


def test_a_wrapper_is_recognized_by_forwarding_never_by_name(tmp_path):
    """The wrapper method is `emit`, its parameter `destination` — nothing is named
    'push' or 'topic' anywhere. Detection is the forwarding fact alone."""
    res = run(tmp_path, tree("""\
public class Caller {
    private Gateway gateway;
    public void go(Object evt) { gateway.emit("named-nothing-like-it", evt); }
}
"""))
    assert [e.dst for e in produces(res)] == ["topic:named-nothing-like-it"]


def test_the_wrappers_own_forwarding_send_is_structure_not_noise(tmp_path):
    """One junk row per wrapper per component was the old behaviour, measured at scale
    on a real estate. The forwarding send now DEFINES the wrapper and reports nothing."""
    res = run(tmp_path, tree("""\
public class Caller {
    private Gateway gateway;
    public void go(Object evt) { gateway.emit("t-1", evt); }
}
"""))
    assert not [u for u in res.unresolved
                if u.get("source_file", "").endswith("Gateway.java")]


def test_a_callers_own_parameter_stays_a_recorded_row(tmp_path):
    """One level of forwarding is the bound. The caller here is itself wrapper-shaped,
    and resolving it would take whole-program dataflow — so it is recorded, not chased."""
    res = run(tmp_path, tree("""\
public class Caller {
    private Gateway gateway;
    public void go(Object evt, String routed) {
        gateway.emit(routed, evt);
    }
}
"""))
    assert not produces(res)
    (row,) = [u for u in res.unresolved if u["kind"] == "unresolved-topic"]
    assert row["raw"] == "routed" and row["source_file"].endswith("Caller.java")


# -- the binding chain --------------------------------------------------------------------


def test_a_getter_argument_resolves_through_the_binding_chain(tmp_path):
    res = run(tmp_path, tree("""\
public class Caller {
    private Gateway gateway;
    private Cfg cfg;
    public void go(Object evt) {
        gateway.emit(cfg.getNotifyTopic(), evt);
    }
}
"""))
    (edge,) = produces(res)
    assert edge.dst == "topic:order-notify-v1"
    assert edge.line == 5 and edge.source_file.endswith("Caller.java")
    assert edge.confidence == 0.9, "the getX -> x convention is the one link that is " \
                                   "convention rather than declaration"
    assert edge.props["property"] == "orders.notify.topic"
    assert edge.props["binding"].endswith("Cfg.java:3")


def test_a_direct_client_send_with_a_getter_also_resolves(tmp_path):
    res = run(tmp_path, tree("""\
public class Caller {
    private KafkaTemplate<String, String> kafkaTemplate;
    private Cfg cfg;
    public void go(Object evt) {
        kafkaTemplate.send(cfg.getStreamTopic(), evt);
    }
}
"""))
    (edge,) = produces(res)
    assert edge.dst == "topic:order-stream-v1"
    assert edge.props["getter"] == "getStreamTopic"


def test_a_convention_getter_needs_the_class_level_licence(tmp_path):
    """Without @Getter/@Data and without a written-out method, getX() names nothing the
    source declares — mapping it anyway would be name-mangling, not reading."""
    files = tree("""\
public class Caller {
    private Gateway gateway;
    private Cfg cfg;
    public void go(Object evt) { gateway.emit(cfg.getStreamTopic(), evt); }
}
""")
    files["src/main/java/x/Cfg.java"] = """\
public class Cfg {
    @Value("${orders.stream.topic}")
    private String streamTopic;
}
"""
    res = run(tmp_path, files)
    assert not produces(res)
    (row,) = [u for u in res.unresolved if u["kind"] == "unresolved-topic"]
    assert "getStreamTopic" in row["raw"]


def test_the_receivers_declared_type_picks_the_class(tmp_path):
    """Two config classes bind the same field name to different keys; the caller's field
    type says which one it holds, so there is nothing to guess."""
    files = tree("""\
public class Caller {
    private Gateway gateway;
    private OtherCfg cfg;
    public void go(Object evt) { gateway.emit(cfg.getNotifyTopic(), evt); }
}
""")
    files["src/main/java/x/OtherCfg.java"] = """\
@Getter
public class OtherCfg {
    @Value("${orders.stream.topic}")
    private String notifyTopic;
}
"""
    res = run(tmp_path, files)
    (edge,) = produces(res)
    assert edge.dst == "topic:order-stream-v1", \
        "OtherCfg's binding, not Cfg's, because the field is declared as OtherCfg"


def test_an_ambiguous_binding_is_reported_never_guessed(tmp_path):
    """Two classes with the SAME name (different files) bind the field differently, and
    the receiver's type cannot separate simple names — so it is a recorded row."""
    files = tree("""\
public class Caller {
    private Gateway gateway;
    private Cfg cfg;
    public void go(Object evt) { gateway.emit(cfg.getNotifyTopic(), evt); }
}
""")
    files["src/main/java/y/Cfg.java"] = """\
@Getter
public class Cfg {
    @Value("${orders.stream.topic}")
    private String notifyTopic;
}
"""
    res = run(tmp_path, files)
    assert not produces(res)
    (row,) = [u for u in res.unresolved if u["kind"] == "unresolved-topic"]
    assert "2" in row["why"]


def test_a_chain_that_reaches_an_unresolvable_value_is_honest(tmp_path):
    """The getter chain works; the property does not resolve (a placeholder with no
    default). The chain must not guess past that — same rule as everywhere else."""
    files = tree("""\
public class Caller {
    private Gateway gateway;
    private Cfg cfg;
    public void go(Object evt) { gateway.emit(cfg.getNotifyTopic(), evt); }
}
""")
    files["src/main/resources/application.properties"] = \
        "orders.notify.topic=${UNSET_ELSEWHERE}\n"
    res = run(tmp_path, files)
    assert not produces(res)
    assert any(u["kind"] == "unresolved-topic" for u in res.unresolved)


# -- the fixture, held to EXPECTED.md ------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("p91")
    B.build(FIXTURE / "workspace.toml", out, run_id="p91")
    import json
    rows = [json.loads(l) for l
            in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    edges = [r for r in rows if "src" in r]
    unresolved = [json.loads(l) for l in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines() if l]
    return edges, unresolved


@pytest.mark.parametrize("line, topic, conf", [
    (18, "topic:order-notify-v1", 0.9),   # P1 — wrapper + written-out getter
    (19, "topic:order-audit-raw", 1.0),   # P2 — wrapper + literal
    (23, "topic:order-stream-v1", 0.9),   # P3 — direct send + convention getter
])
def test_the_fixture_produces_exactly_what_expected_md_claims(built, line, topic, conf):
    edges, _ = built
    (edge,) = [e for e in edges if e["type"] == "DG_PRODUCES"
               and e["source_file"].endswith("AuditTrail.java") and e["line"] == line]
    assert edge["dst"] == topic and edge["confidence"] == conf
    assert edge["src"] == "svc:orders-api@orders-api"


def test_the_fixture_unresolved_settles_at_five(built):
    """The answer-key commit pinned six with two transitional; this commit converts
    them. The survivor is the caller-parameter row — the stated one-level bound."""
    _, unresolved = built
    assert len(unresolved) == 5
    (row,) = [u for u in unresolved if u["kind"] == "unresolved-topic"]
    assert row["raw"] == "routed" and row["line"] == 24
    assert not any(u.get("source_file", "").endswith("EventGateway.java")
                   for u in unresolved)
