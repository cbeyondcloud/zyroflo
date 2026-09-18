"""Publications and subscriptions, reconstructed from client calls in Python source.

The counterpart of the Java messaging extractor beside it, and **its contract is that
module's, not a second one** — read `extractors/kafka/`'s header for why producers and
consumers must be paired on the topic's resolved VALUE and never on the key that names it.
Restating it here would create two copies of the one rule this extractor exists to obey.

## Why this is a separate reader and not a second branch of that one

Nothing in the pairing contract is language-specific; everything in the *evidence* is. There
the subscription is declared by an annotation and the publication is reconstructed from a
send call on a typed field. Here there are no annotations and no types: both halves are
reconstructed from a call on a name bound to a client constructor, so **neither side is
declared** and both are inferences. That difference is why the provenance below is uniform
where the Java reader's is split.

## Not named after a client library

`confluent_kafka`, `kafka-python` and `aiokafka` spell the constructors differently and mean
the same thing, so the factory names are matched as a set and the package is named for the
technology. Naming it after whichever library the first fixture happened to use is the
mistake the migration extractor made one item ago: the name told every adopter of a
different one that they were unsupported, and the globs grew to match until the claim was
true.

**Recorded rather than fixed here:** the Java readers are called `kafka-producers` and
`kafka-consumers` with no language in the name, which reads as universal and is not — they
match `*.java` only. These are suffixed instead, so the report says which is which. That
asymmetry is a milder form of the same smell, and renaming the incumbents belongs in its own
commit with its own regression test rather than riding along inside this one.

## What is cited, and why not the call

The edge carries the line where the topic NAME is bound, with the call's line in props. Two
reasons, and the second is the one that decides it: that is where a human changes which
topic is used, and a service that publishes from five call sites has **one** publication
relationship rather than five — citing the call would put five near-identical edges in the
graph, differing in a field nobody queries on. When the topic is a literal at the call site
there is no binding, and the call's own line is cited.

## Deliberately not here

A client bound anywhere other than module level is not found. Attributing one requires
deciding which scope a name belongs to, and a half-scoped answer produces edges that look
exactly like correct ones. No committed fixture has a case, so building it would mean
asserting behaviour against an imagined input.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import env_keys

#: Constructors that build a publishing client, across the common libraries.
PRODUCER_FACTORIES = frozenset({"Producer", "SerializingProducer", "KafkaProducer",
                                "AIOKafkaProducer"})

#: Constructors that build a subscribing client.
CONSUMER_FACTORIES = frozenset({"Consumer", "DeserializingConsumer", "KafkaConsumer",
                                "AIOKafkaConsumer"})

#: Methods whose first argument is the destination topic.
SEND_METHODS = frozenset({"produce", "send", "send_and_wait"})

#: Methods whose first argument names the topics being read.
SUBSCRIBE_METHODS = frozenset({"subscribe", "assign"})


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def clients(tree: ast.Module, factories: frozenset[str]) -> set[str]:
    """Module-level names bound to a call of one of these client constructors."""
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in factories:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out.add(target.id)
    return out


def topic_args(node: ast.AST) -> list[dict]:
    """The topic references in a call's first argument: one value, or a list of them.

    The list form is what `subscribe` takes and the bare form is what `produce` takes, and
    both arrive here. Anything that is neither a literal nor a plain name is returned as an
    expression so the caller can report it — guessing what an expression evaluates to is how
    a topic that does not exist enters the graph, and nothing downstream could tell it from
    a real one.
    """
    items = node.elts if isinstance(node, (ast.List, ast.Tuple, ast.Set)) else [node]
    out: list[dict] = []
    for item in items:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            out.append({"kind": "literal", "raw": item.value})
        elif isinstance(item, ast.Name):
            out.append({"kind": "name", "raw": item.id})
        else:
            out.append({"kind": "expression", "raw": ast.unparse(item)})
    return out


def client_calls(tree: ast.Module, names: set[str], methods: frozenset[str]) -> list[ast.Call]:
    """Every call of one of these methods on one of these client names, at any depth.

    Depth matters: the call is almost always inside a function while the client and the
    topic name are bound at module level, so a reader confined to the module body sees the
    two halves and never the thing that joins them.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in names \
                and node.func.attr in methods and node.args:
            found.append(node)
    return found



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "empty-topic-value": ('key', 'raw'),
    "unreadable-file": (),
    "python-unparsed": ('error',),
    "unresolved-topic": ('raw',),
}

class _PythonMessagingExtractor:
    """Shared plumbing: parse each non-test source, or say why it could not be read."""

    name = ""
    edge_type = ""
    factories: frozenset[str] = frozenset()
    methods: frozenset[str] = frozenset()
    INPUT_GLOBS = ("*.py",)
    #: The topic's value can come from either configuration layer, so both must have
    #: published before a name is called unresolvable.
    REQUIRES = ("compose-services", "env-keys")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            for path in sorted(ctx.inputs(repo)):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                self._file(res, ctx, repo, path, rel)
        return res

    def _file(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, path: Path,
              rel: str) -> None:
        text, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "kind": "unreadable-file", "component": repo.alias,
                "source_file": rel, "line": 0,
                "why": "this file could not be read, so every topic it declares is missing "
                       "from the graph rather than absent from the code"})
            return
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            res.unresolved.append({
                "extractor": self.name, "kind": "python-unparsed", "component": repo.alias,
                "source_file": rel, "line": exc.lineno or 0, "error": exc.msg,
                "why": "could not be parsed as Python, so every route, call or topic it "
                       "declares is missing from the graph rather than absent from the code"})
            return

        names = clients(tree, self.factories)
        if not names:
            return
        calls = client_calls(tree, names, self.methods)
        if not calls:
            return

        sites = env_keys.binding_sites(tree)
        svc = ids.component_service_id(repo.alias)
        seen: set[tuple[str, int]] = set()
        for call in calls:
            for ref in topic_args(call.args[0]):
                self._one(res, ctx, repo, rel, call, ref, sites, svc, seen)

    def _one(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
             call: ast.Call, ref: dict, sites: dict, svc: str,
             seen: set[tuple[str, int]]) -> None:
        receiver = call.func.value.id       # type: ignore[union-attr]
        if ref["kind"] == "literal":
            value, key, line, resolved_from = ref["raw"], "", call.lineno, ""
        elif ref["kind"] == "name" and ref["raw"] in sites:
            site = sites[ref["raw"]]
            entry = env_keys.resolve(ctx.cache, repo.alias, site["key"])
            if entry is None:
                res.unresolved.append({
                    "extractor": self.name, "kind": "unresolved-topic",
                    "component": repo.alias, "source_file": rel, "line": site["line"],
                    "raw": site["key"],
                    "why": (f"the topic is named by `{site['key']}`, which resolves to "
                            f"nothing in this component, so this cannot be attached to a "
                            f"topic")})
                return
            value, key, line = entry["value"], site["key"], site["line"]
            resolved_from = f"{entry['file']}:{entry['line']}" if entry.get("file") else ""
        else:
            res.unresolved.append({
                "extractor": self.name, "kind": "unresolved-topic",
                "component": repo.alias, "source_file": rel, "line": call.lineno,
                "raw": ref["raw"],
                "why": (f"the destination is `{ref['raw']}`, which is neither a literal nor "
                        f"a name bound to a configuration read, so the topic cannot be known "
                        f"from the source. Guessing from the name is how a topic that does "
                        f"not exist enters the graph")})
            return

        if not value.strip():
            # Belt to the resolver's braces, and it was earned: a key that resolved to the
            # empty string once produced `topic:` — a node named nothing, at the end of a
            # chain that looked healthy at every step. Correct precedence is what stops that
            # happening; refusing to name an entity after an empty value is what stops it
            # ever being invisible again.
            res.unresolved.append({
                "extractor": self.name, "kind": "empty-topic-value",
                "component": repo.alias, "source_file": rel, "line": line,
                "raw": ref["raw"], "key": key,
                "why": (f"`{ref['raw']}` resolves to an empty value, so there is no topic to "
                        f"attach this to. A configured-to-nothing key is a real hole and is "
                        f"reported as one rather than becoming a node named after it")})
            return

        topic = ids.topic_id(value)
        if (topic, line) in seen:
            # One publication relationship, not one per call site.
            return
        seen.add((topic, line))
        res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
        res.nodes.append(Node(id=topic, type="topic", props={"name": value}))
        res.edges.append(Edge(
            src=svc, type=self.edge_type, dst=topic, source_file=rel, line=line,
            extractor=self.name, confidence=1.0, provenance=Provenance.INFERRED,
            props={"raw": ref["raw"], "key": ids.relaxed_key(key) if key else "",
                   "client": receiver, "call_line": call.lineno,
                   "method": call.func.attr,          # type: ignore[union-attr]
                   "resolved_from": resolved_from}))


class PyProducersExtractor(_PythonMessagingExtractor):
    """Publications, reconstructed from a send call on a producer client."""

    name = "kafka-producers-py"
    edge_type = "DG_PRODUCES"
    factories = PRODUCER_FACTORIES
    methods = SEND_METHODS


class PyConsumersExtractor(_PythonMessagingExtractor):
    """Subscriptions, reconstructed from a subscribe call on a consumer client."""

    name = "kafka-consumers-py"
    edge_type = "DG_CONSUMES"
    factories = CONSUMER_FACTORIES
    methods = SUBSCRIBE_METHODS


EXTRACTORS = (PyProducersExtractor(), PyConsumersExtractor())
