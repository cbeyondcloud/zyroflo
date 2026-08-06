"""Which services publish to a topic, and which subscribe to it.

The two halves are found by completely different evidence, and that asymmetry is the whole
difficulty of this extractor rather than an accident of one codebase.

**Subscribing is declared.** A listener annotation names its topics, so the fact is written
down and only needs resolving.

**Publishing is not.** Nothing annotates a producer. The evidence is a send call on a client
object, and the topic is whatever the first argument evaluates to — usually a field bound to
a property somewhere else in the file. So the chain is: find the client, find the send, find
what the argument names, find where that name is bound, resolve the property. Each link can
fail, and the honest answer when one does is an unresolved row rather than a guess.

## Pairing must be on the VALUE, never on the key

A publisher's property and a subscriber's property routinely have different names —
`orders.publish.topic` and `orders.consume.topic` are one topic under two keys. An extractor
that pairs producers to consumers by key name finds nothing and reports a topic with a
producer and no consumer, which reads as a real architectural finding rather than as a bug
in the extractor. Both edges therefore point at a topic node named by the RESOLVED value,
and the pairing falls out of that instead of being computed.

## What is deliberately not inferred

A send whose argument is an expression rather than a name, and a listener whose topics come
from a runtime lookup, are both recorded as unresolved. Guessing from a variable's name is
how a plausible topic that does not exist enters the graph, and there is nothing downstream
that could tell it from a real one.
"""
from __future__ import annotations

from pathlib import Path

from tools.graph.core.lib import ids, repo_walk, srcfacts
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import _java
from tools.graph.extractors.properties import resolve_reference, view_for

#: Annotations that declare a subscription, and the argument naming its topics.
LISTENER_ANNOTATIONS = {"KafkaListener"}
TOPIC_ARGS = ("topics", "topicPattern", "topicPartitions")

#: Field types that identify a publishing client. Matched as a substring, so a generic
#: parameterisation (`KafkaTemplate<String, Order>`) and a subclass both count.
CLIENT_TYPES = ("KafkaTemplate", "KafkaProducer", "Producer<")

#: Methods on such a client whose first argument is the destination topic.
SEND_METHODS = {"send", "sendDefault", "sendOffsetsToTransaction"}


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def topic_list(raw: str) -> list[str]:
    """Split an annotation argument that may be one value or an array of them.

    The array form is `{"a", "b"}` and the placeholder form is `${a.b}`. **Both contain
    braces**, and stripping braces to handle the first mangles the second into
    `${a.b` — which no longer matches the placeholder pattern, so it is taken for a literal
    and becomes a topic named after the configuration key. That is precisely the failure the
    resolver exists to prevent, reintroduced one layer above it, and it produces a graph that
    is confidently, plausibly wrong rather than visibly broken.

    So the array form is recognised by its own shape and nothing else is touched.
    """
    t = raw.strip()
    if t.startswith("{") and t.endswith("}") and '"' in t:
        return [srcfacts.unquote(p.strip()) for p in t[1:-1].split(",") if p.strip()]
    return [t] if t else []


def _topic_node(res: ExtractResult, value: str) -> str:
    tid = ids.topic_id(value)
    res.nodes.append(Node(id=tid, type="topic"))
    return tid


def _svc_node(res: ExtractResult, alias: str) -> str:
    svc = ids.component_service_id(alias)
    res.nodes.append(Node(id=svc, type="svc", props={"component": alias}))
    return svc


def _unresolved(extractor: str, repo: RepoRef, kind: str, rel: str, line: int,
                why: str, **extra) -> dict:
    return {"extractor": extractor, "component": repo.alias, "kind": kind,
            "source_file": rel, "line": line, "why": why, **extra}


class _JavaSourceExtractor:
    """Shared plumbing: parse each non-test source, or say why it could not be read."""

    name = ""
    INPUT_GLOBS = ("*.java",)
    #: Topics are named by configuration keys, so the resolved view must exist first.
    REQUIRES = ("properties",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not _java.available():
            for repo in ctx.repos:
                res.unresolved.append(_unresolved(
                    self.name, repo, "grammar-unavailable", "", 0,
                    f"no parser is installed for this language, so {len(ctx.inputs(repo))} "
                    f"file(s) were not read; any edge they declare is missing from the graph, "
                    f"and its absence here is the only record of that"))
            return res

        for repo in ctx.repos:
            view = view_for(ctx.cache, repo.alias)
            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                try:
                    parsed = _java.parse(repo_walk.read_text(path))
                except Exception:
                    res.unresolved.append(_unresolved(
                        self.name, repo, "unparsable", rel, 0,
                        "the parser could not read this file, so any topic it names is "
                        "missing from the graph"))
                    continue
                self.file(res, repo, rel, parsed, view)
        return res

    def file(self, res, repo, rel, parsed, view) -> None:   # pragma: no cover - overridden
        raise NotImplementedError


class ConsumersExtractor(_JavaSourceExtractor):
    """Subscriptions, read from the annotation that declares them."""

    name = "kafka-consumers"

    def file(self, res: ExtractResult, repo: RepoRef, rel: str,
             parsed: _java.JavaFile, view: dict | None) -> None:
        for cls in parsed.classes:
            for member in (*cls.methods, *cls.fields):
                ann = member.annotation(*LISTENER_ANNOTATIONS)
                if ann is None:
                    continue
                raw = next((ann.args[a] for a in TOPIC_ARGS if a in ann.args), ann.value)
                if not raw:
                    res.unresolved.append(_unresolved(
                        self.name, repo, "listener-without-topic", rel, ann.line,
                        "a subscription is declared here and names no topic in its "
                        "arguments, so what it consumes cannot be read from the source"))
                    continue
                for one in topic_list(raw):
                    self._one(res, repo, rel, ann.line, one, view, cls.name, member.name)

    def _one(self, res, repo, rel, line, raw, view, cls_name, member_name) -> None:
        hit = resolve_reference(view, raw)
        if hit is None:
            res.unresolved.append(_unresolved(
                self.name, repo, "unresolved-topic", rel, line, raw=raw,
                why=("the topic is named by a configuration key that resolves to nothing in "
                     "this component, so the subscription cannot be attached to a topic")))
            return
        svc, topic = _svc_node(res, repo.alias), _topic_node(res, hit["value"])
        res.edges.append(Edge(
            src=svc, type="DG_CONSUMES", dst=topic, source_file=rel, line=line,
            extractor=self.name, confidence=1.0,
            provenance=Provenance.EXTRACTED if not hit["key"] else Provenance.INFERRED,
            props={"raw": raw, "property": hit["key"], "declared_class": cls_name,
                   "declared_member": member_name,
                   "resolved_from": f"{hit['file']}:{hit['line']}" if hit["file"] else ""}))


class ProducersExtractor(_JavaSourceExtractor):
    """Publications, reconstructed from a send call on a client object."""

    name = "kafka-producers"

    def file(self, res: ExtractResult, repo: RepoRef, rel: str,
             parsed: _java.JavaFile, view: dict | None) -> None:
        for cls in parsed.classes:
            clients = {f.name for f in cls.fields
                       if any(t in f.type for t in CLIENT_TYPES)}
            if not clients:
                continue
            # Where each field gets its value. The annotation's line is what a reader is
            # sent to: it is where the topic NAME is bound, which is the fact being cited,
            # whereas the send call is only where it is used.
            bindings = {f.name: a for f in cls.fields for a in f.annotations
                        if a.value and "${" in a.value}

            for call in parsed.invocations:
                if call.receiver not in clients or call.method not in SEND_METHODS:
                    continue
                if not call.args:
                    continue
                self._one(res, repo, rel, call, bindings, view, cls.name)

    def _one(self, res, repo, rel, call, bindings, view, cls_name) -> None:
        arg = call.args[0]
        if arg in bindings:
            ann = bindings[arg]
            raw, line = ann.value, ann.line
        elif arg.startswith(('"', "'")):
            raw, line = srcfacts.unquote(arg), call.line
        else:
            res.unresolved.append(_unresolved(
                self.name, repo, "unresolved-topic", rel, call.line, raw=arg,
                why=(f"the destination is the expression {arg!r}, which is not a literal and "
                     f"not a field bound to a configuration key, so the topic cannot be known "
                     f"from the source. Guessing from the name is how a topic that does not "
                     f"exist enters the graph")))
            return

        hit = resolve_reference(view, raw)
        if hit is None:
            res.unresolved.append(_unresolved(
                self.name, repo, "unresolved-topic", rel, line, raw=raw,
                why=("the destination is named by a configuration key that resolves to "
                     "nothing in this component, so the publication cannot be attached to a "
                     "topic")))
            return

        svc, topic = _svc_node(res, repo.alias), _topic_node(res, hit["value"])
        res.edges.append(Edge(
            src=svc, type="DG_PRODUCES", dst=topic, source_file=rel, line=line,
            extractor=self.name, confidence=1.0, provenance=Provenance.INFERRED,
            props={"raw": raw, "property": hit["key"], "declared_class": cls_name,
                   "client": call.receiver, "send_line": call.line,
                   "resolved_from": f"{hit['file']}:{hit['line']}" if hit["file"] else ""}))


EXTRACTORS = (ProducersExtractor(), ConsumersExtractor())
