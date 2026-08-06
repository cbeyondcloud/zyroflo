"""Shared source facts for one language — NOT an extractor.

The leading underscore is load-bearing: the registry skips any directory starting with `_`
or `.`, so this package is importable by the extractors that need it without registering as
one itself. It exists because several technologies live in the same source files — a class
can carry a web mapping, a message listener and an injected property at once — and three
extractors each re-deriving "what annotations does this class have" is three chances to
disagree about the answer.

It knows about a language, never about a workspace, and it emits nothing: it turns a parse
tree into small records and stops there. Deciding that `@GetMapping` means an endpoint is
the web extractor's job; deciding that a listener annotation means a subscription is the
messaging extractor's. This package only says what is written.

**Structure, not text.** Everything here reads the parse tree. That is the point of parsing
at all: a mapping split across lines, a nested class, a path inside a comment and a path
inside a text block are all structure to a parser and accidents to a regex — and the
accidents fail in the direction that invents edges rather than the direction that misses
them, which is the more expensive way to be wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tools.graph.core.lib import srcfacts

LANGUAGE = "java"

#: Node types that hold a declared type, in the order tree-sitter nests them.
_TYPE_NODES = {
    "type_identifier", "generic_type", "scoped_type_identifier", "array_type",
    "integral_type", "floating_point_type", "boolean_type", "void_type",
}


@dataclass(frozen=True, slots=True)
class Annotation:
    """One annotation. `value` is the single unnamed argument; `args` the named ones."""

    name: str
    line: int
    value: str | None = None
    args: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Member:
    """A field or a method: its name, where it starts, and what decorates it."""

    name: str
    line: int
    type: str = ""
    annotations: tuple[Annotation, ...] = ()

    def annotation(self, *names: str) -> Annotation | None:
        want = set(names)
        return next((a for a in self.annotations if a.name in want), None)


@dataclass(frozen=True, slots=True)
class Invocation:
    """A method call: `receiver.method(...)`, with each argument's source text."""

    receiver: str
    method: str
    args: tuple[str, ...]
    line: int


@dataclass(frozen=True, slots=True)
class JavaClass:
    name: str
    line: int
    annotations: tuple[Annotation, ...] = ()
    methods: tuple[Member, ...] = ()
    fields: tuple[Member, ...] = ()


@dataclass(frozen=True, slots=True)
class JavaFile:
    classes: tuple[JavaClass, ...] = ()
    invocations: tuple[Invocation, ...] = ()
    literals: tuple[srcfacts.Capture, ...] = ()


def available() -> bool:
    """Whether this language can be parsed here.

    Callers check it and record an unresolved row when it is False. An extractor that simply
    returned nothing would be indistinguishable from a component with no such code — the
    empty-reads-as-clean failure, one layer down.
    """
    return srcfacts.available(LANGUAGE)


def _named(node: Any, field_name: str) -> Any:
    return node.child_by_field_name(field_name)


def _annotations(sf: srcfacts.SourceFile, node: Any) -> tuple[Annotation, ...]:
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    if mods is None:
        return ()
    out: list[Annotation] = []
    for a in mods.children:
        if a.type not in ("annotation", "marker_annotation"):
            continue
        name_node = _named(a, "name")
        # A fully-qualified annotation is written `@org.example.Thing`; only the last
        # segment identifies it, and an extractor matching on the whole path would miss it.
        name = sf.text_of(name_node).rsplit(".", 1)[-1] if name_node is not None else ""
        args_node = _named(a, "arguments")
        value, named = None, {}
        if args_node is not None:
            pairs = [c for c in args_node.children if c.type == "element_value_pair"]
            for p in pairs:
                k, v = _named(p, "key"), _named(p, "value")
                if k is not None and v is not None:
                    named[sf.text_of(k)] = srcfacts.unquote(sf.text_of(v))
            if not pairs:
                lone = next((c for c in args_node.children if c.is_named), None)
                if lone is not None:
                    value = srcfacts.unquote(sf.text_of(lone))
        out.append(Annotation(name=name, line=a.start_point[0] + 1, value=value, args=named))
    return tuple(out)


def _type_text(sf: srcfacts.SourceFile, node: Any) -> str:
    t = _named(node, "type")
    if t is not None:
        return sf.text_of(t)
    child = next((c for c in node.children if c.type in _TYPE_NODES), None)
    return sf.text_of(child) if child is not None else ""


def _field_members(sf: srcfacts.SourceFile, node: Any) -> list[Member]:
    """One Member per declarator, so `int a, b;` yields two.

    The line is the DECLARATION's, not the declarator's: a field carrying an annotation
    starts at the annotation, and that is the line a reader is sent to when asked where a
    value is bound.
    """
    anns = _annotations(sf, node)
    type_text = _type_text(sf, node)
    line = node.start_point[0] + 1
    out = []
    for d in node.children:
        if d.type != "variable_declarator":
            continue
        n = _named(d, "name")
        if n is not None:
            out.append(Member(name=sf.text_of(n), line=line, type=type_text, annotations=anns))
    return out


def _class_at(sf: srcfacts.SourceFile, node: Any) -> JavaClass:
    name_node = _named(node, "name")
    body = _named(node, "body")
    methods: list[Member] = []
    fields: list[Member] = []
    if body is not None:
        for child in body.children:
            if child.type == "method_declaration":
                n = _named(child, "name")
                if n is not None:
                    methods.append(Member(name=sf.text_of(n),
                                          line=child.start_point[0] + 1,
                                          type=_type_text(sf, child),
                                          annotations=_annotations(sf, child)))
            elif child.type == "field_declaration":
                fields.extend(_field_members(sf, child))
    return JavaClass(
        name=sf.text_of(name_node) if name_node is not None else "",
        line=node.start_point[0] + 1,
        annotations=_annotations(sf, node),
        methods=tuple(methods),
        fields=tuple(fields),
    )


def parse(text: str) -> JavaFile:
    """Parse one source file into records. Raises `srcfacts.LanguageUnavailable`.

    Nested classes are returned as their own entries and never folded into the outer one:
    a mapping declared on an inner class belongs to that class, and merging them would
    attribute an endpoint to a type that does not declare it.
    """
    sf = srcfacts.parse(LANGUAGE, text)

    classes = [_class_at(sf, n) for n in sf.walk("class_declaration")]

    invocations: list[Invocation] = []
    for n in sf.walk("method_invocation"):
        obj, name, args = _named(n, "object"), _named(n, "name"), _named(n, "arguments")
        invocations.append(Invocation(
            receiver=sf.text_of(obj) if obj is not None else "",
            method=sf.text_of(name) if name is not None else "",
            args=tuple(sf.text_of(c) for c in (args.children if args is not None else [])
                       if c.is_named),
            line=n.start_point[0] + 1,
        ))

    return JavaFile(classes=tuple(classes), invocations=tuple(invocations),
                    literals=tuple(sf.string_literals("string_literal")))
