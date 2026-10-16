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
from tools.graph.extractors import _bindings, _java
from tools.graph.extractors.properties import (reference_ambiguity, resolve_reference,
                                               view_for)

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


def _empty_topic(res: ExtractResult, extractor: str, repo: RepoRef, rel: str, line: int,
                 raw: str, hit: dict) -> bool:
    """Record a reference that resolved to the empty string; True when it did.

    T535, `kafka_python`'s guard in the Java paths: a key configured to nothing - or an
    inline empty default, `${key:}` - made a node named `topic:`, and every such key in the
    workspace merged onto it, asserting producer and consumer links between components that
    share nothing. This module declared the `empty-topic-value` kind and never emitted it.
    """
    if str(hit.get("value", "")).strip():
        return False
    res.unresolved.append(_unresolved(
        extractor, repo, "empty-topic-value", rel, line, raw=raw, key=hit.get("key", ""),
        why=(f"`{raw}` resolves to an empty value, so there is no topic to attach this to. "
             f"A configured-to-nothing key is a real hole and is reported as one rather "
             f"than becoming a node named after nothing")))
    return True


def _unresolved(extractor: str, repo: RepoRef, kind: str, rel: str, line: int,
                why: str, **extra) -> dict:
    return {"extractor": extractor, "component": repo.alias, "kind": kind,
            "source_file": rel, "line": line, "why": why, **extra}


def _why_unresolved(view: dict | None, raw: str, subject: str) -> str:
    """Why a topic reference resolved to nothing — and the two reasons are different facts.

    A key nobody declares and a key two scopes declare DIFFERENTLY both return None, but
    the second is not "resolves to nothing": it resolves to several things and the reading
    file selects none of them, so naming one would be a guess. Before the scope layer that
    guess was made silently, and this component's reader was pointed at another
    application's topic with a straight face.
    """
    contested = reference_ambiguity(view, raw)
    if contested is not None:
        return (f"the topic key resolves to {len(contested['values'])} different values "
                f"across {len(contested['scopes'])} configuration scopes of this component "
                f"and the reading file's location selects none of them, so {subject} cannot "
                f"be attached to a topic without guessing which application this file "
                f"belongs to")
    return (f"the topic is named by a configuration key that resolves to nothing in "
            f"this component, so {subject} cannot be attached to a topic")



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
#: The parsers this module's readers can lack (batch 16, the owner's ruling 30): a shard
#: records each one's version when it was built, or that it was missing (batch 22).
PARSERS = ("java",)

UNRESOLVED_FIELDS = {
    "empty-topic-value": ('key', 'raw'),
    "grammar-unavailable": ('language',),
    "listener-without-topic": (),
    "unparsable": ('error',),
    "unresolved-topic": ('raw',),
}

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
                    f"no parser is installed for java, so {len(ctx.inputs(repo))} "
                    f"file(s) were not read; any edge they declare is missing from the graph, "
                    f"and its absence here is the only record of that",
                    # Promised above and absent from the row until item 86. It said "this
                    # language" and named none, which is the defect this kind exists to
                    # prevent, committed by the row reporting it.
                    language="java"))
            return res

        for repo in ctx.repos:
            view = view_for(ctx.cache, repo.alias)
            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                try:
                    parsed = _java.parse(repo_walk.read_text(path))
                except Exception as exc:
                    res.unresolved.append(_unresolved(
                        self.name, repo, "unparsable", rel, 0,
                        "the parser could not read this file, so any topic it names is "
                        "missing from the graph",
                        error=str(exc)))
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
                topics = topic_list(raw)
                if not topics:
                    # T574: written, but naming nothing (`topics = "   "`) - stripped to
                    # no topic, it made no node, no edge and no row. The empty-topic row.
                    _empty_topic(res, self.name, repo, rel, ann.line, raw,
                                 {"value": "", "key": ""})
                    continue
                for one in topics:
                    self._one(res, repo, rel, ann.line, one, view, cls.name, member.name)

    def _one(self, res, repo, rel, line, raw, view, cls_name, member_name) -> None:
        hit = resolve_reference(view, raw, rel)
        if hit is None:
            res.unresolved.append(_unresolved(
                self.name, repo, "unresolved-topic", rel, line, raw=raw,
                why=_why_unresolved(view, raw, "the subscription")))
            return
        if _empty_topic(res, self.name, repo, rel, line, raw, hit):
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
    """Publications, reconstructed from a send call on a client object — or on a WRAPPER.

    Item 91, measured before designed: on a real estate the dominant idiom routes every
    publication through a component-local wrapper (584 getter-argument call sites against
    47 literals), so the send call this extractor used to read had a method parameter
    where the topic belongs. Three additions, each a declared link:

    * **A wrapper is a method whose own parameter forwards into a client send** — found
      by that forwarding fact, never by name. Its internal send stops being an
      unresolved row (one junk row per wrapper per component at estate scale): the fact
      it carries moves to the call sites, where the answer actually is.
    * **A getter argument resolves through the component's config-class bindings**
      (`_bindings`): the receiver's declared field type picks the class, `getX -> x`
      picks the field — licenced by a written-out getter or a class-level Lombok
      `@Getter`/`@Data` — and the field's `@Value` key resolves like any other
      reference. Getter-mediated edges carry confidence 0.9: the name convention is
      the one link in the chain that is convention rather than declaration.
    * **One level of forwarding is the bound.** A caller passing its own parameter into
      a wrapper is wrapper-shaped itself; resolving it is whole-program dataflow, so it
      is recorded, not chased.
    """

    name = "kafka-producers"

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        """Two passes per component: parse everything (wrappers and bindings are facts
        about OTHER files than the call sites), then resolve. The base class's per-file
        walk cannot see across files, so this override collects first."""
        res = ExtractResult()
        if not _java.available():
            for repo in ctx.repos:
                res.unresolved.append(_unresolved(
                    self.name, repo, "grammar-unavailable", "", 0,
                    f"no parser is installed for java, so {len(ctx.inputs(repo))} "
                    f"file(s) were not read; any edge they declare is missing from the "
                    f"graph, and its absence here is the only record of that",
                    language="java"))
            return res
        for repo in ctx.repos:
            view = view_for(ctx.cache, repo.alias)
            files: list[tuple[str, _java.JavaFile]] = []
            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                try:
                    files.append((rel, _java.parse(repo_walk.read_text(path))))
                except Exception as exc:
                    res.unresolved.append(_unresolved(
                        self.name, repo, "unparsable", rel, 0,
                        "the parser could not read this file, so any topic it names is "
                        "missing from the graph",
                        error=str(exc)))
            self.component(res, repo, files, view)
        return res

    @staticmethod
    def _clients(cls: _java.JavaClass) -> set[str]:
        return {f.name for f in cls.fields if any(t in f.type for t in CLIENT_TYPES)}

    @staticmethod
    def wrappers_of(files: list[tuple[str, _java.JavaFile]]) -> dict[str, dict[str, int]]:
        """class name -> {method name: index of the forwarded (topic) parameter}."""
        found: dict[str, dict[str, int]] = {}
        for _, parsed in files:
            for cls in parsed.classes:
                clients = ProducersExtractor._clients(cls)
                if not clients:
                    continue
                params_of = {m.name: m.params for m in cls.methods}
                for call in parsed.invocations:
                    if (call.receiver in clients and call.method in SEND_METHODS
                            and call.args):
                        params = params_of.get(call.in_method, ())
                        if call.args[0] in params:
                            found.setdefault(cls.name, {})[call.in_method] = \
                                params.index(call.args[0])
        return found

    def component(self, res: ExtractResult, repo: RepoRef,
                  files: list[tuple[str, _java.JavaFile]], view: dict | None) -> None:
        registry = _bindings.class_registry(files)
        wrappers = self.wrappers_of(files)
        for rel, parsed in files:
            for cls in parsed.classes:
                clients = self._clients(cls)
                field_types = {f.name: _bindings.strip_generics(f.type)
                               for f in cls.fields}
                value_fields = {f.name: a for f in cls.fields for a in f.annotations
                                if a.value and "${" in a.value}
                params_of = {m.name: m.params for m in cls.methods}
                for call in parsed.invocations:
                    if call.receiver in clients and call.method in SEND_METHODS \
                            and call.args:
                        params = params_of.get(call.in_method, ())
                        if (call.args[0] in params
                                and wrappers.get(cls.name, {}).get(call.in_method)
                                == params.index(call.args[0])):
                            continue    # the wrapper's own forwarding send IS the
                            #             wrapper fact; its callers carry the topic
                        self._one(res, repo, rel, call, call.args[0], value_fields,
                                  registry, field_types, view, cls.name, via="client")
                        continue
                    wcls = field_types.get(call.receiver, "")
                    idx = wrappers.get(wcls, {}).get(call.method)
                    if idx is not None and len(call.args) > idx:
                        self._one(res, repo, rel, call, call.args[idx], value_fields,
                                  registry, field_types, view, cls.name, via="wrapper")

    def _one(self, res, repo, rel, call, arg, value_fields, registry, field_types,
             view, cls_name, via) -> None:
        confidence, getter, binding_cite = 1.0, "", ""
        if arg in value_fields:
            ann = value_fields[arg]
            raw, line = ann.value, ann.line
        elif arg.startswith(('"', "'")):
            raw, line = srcfacts.unquote(arg), call.line
        elif m := _bindings.GETTER_CALL.match(arg):
            recv, getter = m.group(1), m.group(2)
            hits = _bindings.lookup(registry, field_types.get(recv, ""), getter)
            if len(hits) != 1:
                res.unresolved.append(_unresolved(
                    self.name, repo, "unresolved-topic", rel, call.line, raw=arg,
                    why=(f"{len(hits)} classes named {field_types.get(recv, recv)!r} "
                         f"bind the field behind {getter!r} to different keys, and this "
                         f"layer cannot tell simple names apart, so naming one would be "
                         f"a guess" if hits else
                         f"no config class declares a binding {getter!r} can reach — "
                         f"either nothing binds that field or nothing licences the "
                         f"getter — so the topic cannot be known from the source")))
                return
            b = hits[0]
            raw, line = b.raw, call.line
            confidence, binding_cite = 0.9, f"{b.file}:{b.line}"
            rel_for_scope = b.file
        else:
            res.unresolved.append(_unresolved(
                self.name, repo, "unresolved-topic", rel, call.line, raw=arg,
                why=(f"the destination is the expression {arg!r}, which is not a literal, "
                     f"not a field bound to a configuration key, and not a getter this "
                     f"component's bindings can answer, so the topic cannot be known "
                     f"from the source. Guessing from the name is how a topic that does "
                     f"not exist enters the graph")))
            return

        hit = resolve_reference(view, raw, rel if not binding_cite else rel_for_scope)
        if hit is None:
            res.unresolved.append(_unresolved(
                self.name, repo, "unresolved-topic", rel, line, raw=raw,
                why=_why_unresolved(view, raw, "the publication")))
            return
        if _empty_topic(res, self.name, repo, rel, line, raw, hit):
            return

        svc, topic = _svc_node(res, repo.alias), _topic_node(res, hit["value"])
        props = {"raw": raw, "property": hit["key"], "declared_class": cls_name,
                 "client": call.receiver, "send_line": call.line, "via": via,
                 "resolved_from": f"{hit['file']}:{hit['line']}" if hit["file"] else ""}
        if getter:
            props["getter"] = getter
            props["binding"] = binding_cite
        res.edges.append(Edge(
            src=svc, type="DG_PRODUCES", dst=topic, source_file=rel, line=line,
            extractor=self.name, confidence=confidence, provenance=Provenance.INFERRED,
            props=props))


EXTRACTORS = (ProducersExtractor(), ConsumersExtractor())
