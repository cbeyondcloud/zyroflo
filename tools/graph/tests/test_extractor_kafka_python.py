"""Publications and subscriptions reconstructed from Python client calls.

The counterpart of the Java messaging tests, exercising the same contracts — pair on the
resolved value, never guess a topic from a name — through syntax that shares nothing with
them.

Everything here is synthetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import compose, env_keys, kafka_python  # noqa: E402

PRODUCERS = kafka_python.PyProducersExtractor()
CONSUMERS = kafka_python.PyConsumersExtractor()

PUBLISHER = '''\
import os

from confluent_kafka import Producer

QUEUE = os.environ["RESTOCK_QUEUE"]

_producer = Producer({"bootstrap.servers": "broker:9092"})


def publish(sku: str) -> None:
    _producer.produce(QUEUE, key=sku)
'''

CONSUMER = '''\
import os

from confluent_kafka import Consumer

SOURCE = os.getenv("RESTOCK_SOURCE", "restock-events-v2")

_consumer = Consumer({"group.id": "restock"})


def run() -> None:
    _consumer.subscribe([SOURCE])
'''


def ctx_for(tmp_path: Path, files: dict[str, str], alias: str = "svc",
            cache: dict | None = None) -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: paths}, cache=cache or {})


def deployed(alias: str, key: str, value: str, line: int = 10) -> dict:
    return {compose.CACHE_KEY: {alias: {"values": {ids.relaxed_key(key): {
        "value": value, "file": "docker-compose.yml", "line": line, "raw": value,
        "spellings": {}}}, "unresolved": {}}}}


# ── the two halves, and the value that joins them ────────────────────────────


def test_a_produce_call_names_the_topic_its_argument_resolves_to(tmp_path: Path):
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": PUBLISHER},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert [(e.type, e.dst) for e in res.edges] == [
        ("DG_PRODUCES", ids.topic_id("events-v2"))]


def test_a_subscribe_call_reads_its_list_argument(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"c.py": CONSUMER})
    env_keys.EnvKeysExtractor().extract(ctx)
    res = CONSUMERS.extract(ctx)
    assert [(e.type, e.dst) for e in res.edges] == [
        ("DG_CONSUMES", ids.topic_id("restock-events-v2"))]


def test_the_two_sides_meet_at_the_value_and_nowhere_else(tmp_path: Path):
    """The claim the whole extractor exists to satisfy. The publisher's key, file, component
    and resolution layer all differ from the consumer's; only the resolved value is shared.
    An extractor pairing on the key finds nothing and reports a topic with a producer and no
    consumer — which reads as a real architectural finding rather than as a bug."""
    pub = ctx_for(tmp_path / "a", {"p.py": PUBLISHER}, alias="api",
                  cache=deployed("api", "RESTOCK_QUEUE", "restock-events-v2"))
    con = ctx_for(tmp_path / "b", {"c.py": CONSUMER}, alias="worker")
    env_keys.EnvKeysExtractor().extract(con)
    produced = PRODUCERS.extract(pub).edges[0]
    consumed = CONSUMERS.extract(con).edges[0]
    assert produced.dst == consumed.dst == ids.topic_id("restock-events-v2")
    assert produced.props["key"] != consumed.props["key"], "the keys must differ, or the "\
        "fixture is not testing what it claims"


# ── what is cited, and why not the call ──────────────────────────────────────


def test_the_cited_line_is_where_the_topic_name_is_bound(tmp_path: Path):
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": PUBLISHER},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert res.edges[0].line == 5                    # QUEUE = os.environ[...]
    assert res.edges[0].props["call_line"] == 11     # _producer.produce(QUEUE, ...)


def test_many_call_sites_are_one_publication_relationship(tmp_path: Path):
    """Citing the call site would put four near-identical edges in the graph, differing
    only in a field nobody queries on."""
    body = PUBLISHER + "\n\ndef again(sku):\n    _producer.produce(QUEUE)\n    _producer.produce(QUEUE)\n"
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert len(res.edges) == 1


def test_a_literal_topic_cites_the_call_because_there_is_no_binding(tmp_path: Path):
    body = PUBLISHER.replace("_producer.produce(QUEUE, key=sku)",
                             '_producer.produce("events-v2", key=sku)')
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body}))
    assert res.edges[0].line == 11 and res.edges[0].props["key"] == ""


# ── refusals, because a plausible wrong topic cannot be told from a real one ──


def test_an_expression_destination_is_reported_not_guessed(tmp_path: Path):
    body = PUBLISHER.replace("_producer.produce(QUEUE, key=sku)",
                             '_producer.produce(prefix + "-events", key=sku)')
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body}))
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["unresolved-topic"]


def test_a_name_that_is_not_a_configuration_read_is_reported(tmp_path: Path):
    body = PUBLISHER.replace('QUEUE = os.environ["RESTOCK_QUEUE"]', 'QUEUE = build_name()')
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body}))
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["unresolved-topic"]


def test_a_key_that_resolves_to_nothing_is_reported(tmp_path: Path):
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": PUBLISHER}))
    assert res.edges == []
    assert res.unresolved[0]["kind"] == "unresolved-topic"


def test_a_key_resolving_to_an_empty_value_never_becomes_a_node(tmp_path: Path):
    """REGRESSION. This shipped once: a key resolved to `""` and the graph gained a node
    called `topic:` — named nothing, at the end of a chain that looked healthy at every
    step. Empty reading exactly like clean is this project's most expensive failure class."""
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": PUBLISHER},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "")))
    assert res.edges == [] and [n.id for n in res.nodes] == []
    assert [r["kind"] for r in res.unresolved] == ["empty-topic-value"]


# ── scoping: what counts as a client ─────────────────────────────────────────


def test_a_send_on_something_that_is_not_a_client_is_ignored(tmp_path: Path):
    body = ('import os\nQUEUE = os.environ["RESTOCK_QUEUE"]\n'
            'logger = Logger()\n\n\ndef f():\n    logger.send(QUEUE)\n')
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert res.edges == [] and res.unresolved == []


def test_a_consumer_client_does_not_answer_the_producer_extractor(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"c.py": CONSUMER})
    env_keys.EnvKeysExtractor().extract(ctx)
    assert PRODUCERS.extract(ctx).edges == []


def test_a_client_bound_inside_a_function_is_not_found(tmp_path: Path):
    """Pins a stated limitation. Attributing one means deciding which scope a name belongs
    to, and a half-scoped answer produces edges that look exactly like correct ones."""
    body = ('import os\nfrom confluent_kafka import Producer\n'
            'QUEUE = os.environ["RESTOCK_QUEUE"]\n\n\n'
            'def publish():\n    p = Producer({})\n    p.produce(QUEUE)\n')
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert res.edges == []


def test_a_test_file_is_not_read(tmp_path: Path):
    res = PRODUCERS.extract(ctx_for(tmp_path, {"test_p.py": PUBLISHER},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert res.edges == [] and res.unresolved == []


def test_a_file_that_will_not_parse_is_recorded_not_fatal(tmp_path: Path):
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": PUBLISHER, "bad.py": "def (:\n"},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert [r["kind"] for r in res.unresolved] == ["python-unparsed"]
    assert res.edges, "the readable file must still produce its edge"


@pytest.mark.parametrize("factory", ["Producer", "KafkaProducer", "SerializingProducer",
                                     "AIOKafkaProducer"])
def test_the_common_client_libraries_all_count(tmp_path: Path, factory: str):
    """Named for the technology, not for whichever library the first fixture used — the
    mistake the migration extractor made one item ago, where the name told adopters of a
    different tool that they were unsupported and the globs grew until that was true."""
    body = PUBLISHER.replace("Producer({", f"{factory}({{").replace(
        "import Producer", f"import {factory}")
    res = PRODUCERS.extract(ctx_for(tmp_path, {"p.py": body},
                                    cache=deployed("svc", "RESTOCK_QUEUE", "events-v2")))
    assert len(res.edges) == 1


# ── the resolver's layer order, which this fixture proved wrong the hard way ──


def test_the_deployment_layer_outranks_an_inline_default(tmp_path: Path):
    """REGRESSION. This shipped the other way round on the reading that the two layers are
    disjoint — a key read WITH a default never needs the deployment, a key read WITHOUT one
    has no default. True per read site, false per KEY: relaxed binding collapses spellings,
    so a default written beside one spelling answers for all of them."""
    cache = deployed("svc", "RESTOCK_QUEUE", "restock-events-v2")
    cache[env_keys.CACHE_KEY] = {"svc": {"values": {ids.relaxed_key("restock.queue"): {
        "value": "", "file": "settings.py", "line": 10, "raw": "", "spellings": {}}},
        "unresolved": {}}}
    assert env_keys.resolve(cache, "svc", "RESTOCK_QUEUE")["value"] == "restock-events-v2"


def test_an_empty_inline_default_cannot_shadow_a_real_deployment_value(tmp_path: Path):
    """The same regression end to end, which is how it was actually found: the topic a whole
    component publishes to resolved to the empty string, and nothing in the graph said so."""
    cache = deployed("svc", "RESTOCK_QUEUE", "restock-events-v2")
    ctx = ctx_for(tmp_path, {"settings.py": 'import os\nQ = os.environ.get("restock.queue", "")\n',
                             "p.py": PUBLISHER}, cache=cache)
    env_keys.EnvKeysExtractor().extract(ctx)
    res = PRODUCERS.extract(ctx)
    assert [e.dst for e in res.edges] == [ids.topic_id("restock-events-v2")]
