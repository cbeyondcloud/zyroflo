"""Config-class bindings: which field carries which configuration key, per class.

The estate idiom items 91 and 92 both stand on: values reach code through a class whose
fields are `@Value("${key}")`-bound, consumed elsewhere through getters. This helper
reads the DECLARED half of that — bindings per class, getter-to-field mapping — so a
call-site consumer (`kafka` for topics, `spring` for hosts) can resolve `cfg.getX()`
without either of them re-deriving what a binding is.

Two rules carry all the honesty:

* **A getter maps to a field the class actually has** — through a written-out `getX()`
  method, or under a class-level Lombok `@Getter`/`@Data` by the `getX -> x` convention.
  No annotation, no method: no mapping. Name-mangling without that licence would invent
  bindings on any class with a coincidental method name.
* **Simple names can collide.** Java types resolve through imports this layer does not
  read, so two classes named `Cfg` in different files are indistinguishable here. When
  both bind the same field name to the same key, the agreement is the answer; when they
  disagree, the lookup says AMBIGUOUS and the consumer records a row — naming one would
  be a guess wearing provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tools.graph.extractors import _java

#: Class-level Lombok annotations that generate a getter for every field.
GETTER_LICENCES = {"Getter", "Data"}

#: A call argument of the shape `receiver.getSomething()` — the one shape this layer
#: resolves. Anything else is the consumer's problem to record.
GETTER_CALL = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*(get[A-Z][A-Za-z0-9_$]*)\(\s*\)$")

@dataclass(frozen=True, slots=True)
class Binding:
    """One `@Value` field: the raw `${...}` text and where it is declared."""

    raw: str
    field: str
    cls: str
    file: str
    line: int


def field_of_getter(getter: str) -> str:
    """`getNotifyTopic` -> `notifyTopic` — the Lombok convention, also what a
    written-out getter is named after in practice."""
    tail = getter[3:]
    return tail[:1].lower() + tail[1:] if tail else ""


def strip_generics(type_text: str) -> str:
    return type_text.split("<", 1)[0].strip()


def class_registry(files: list[tuple[str, _java.JavaFile]]) -> dict[str, list]:
    """Every class's bindings and getter surface, keyed by SIMPLE class name.

    The value is a list because simple names collide (see the module docstring); each
    entry is `(bindings: dict[field -> Binding], gettable: set[field])`.
    """
    registry: dict[str, list] = {}
    for rel, parsed in files:
        for cls in parsed.classes:
            bindings: dict[str, Binding] = {}
            for f in cls.fields:
                ann = f.annotation("Value")
                if ann is not None and ann.value and "${" in ann.value:
                    bindings[f.name] = Binding(raw=ann.value, field=f.name,
                                               cls=cls.name, file=rel, line=ann.line)
            if not bindings:
                continue
            licenced = any(a.name in GETTER_LICENCES for a in cls.annotations)
            gettable = set(bindings) if licenced else set()
            for m in cls.methods:
                if m.name.startswith("get"):
                    field = field_of_getter(m.name)
                    if field in bindings:
                        gettable.add(field)
            registry.setdefault(cls.name, []).append((bindings, gettable))
    return registry


def lookup(registry: dict[str, list], receiver_type: str, getter: str) -> list[Binding]:
    """The Bindings a `receiver.getX()` call could reach: `[]` (no class binds that
    field, or nothing licences the getter), exactly one, or several that DISAGREE.

    Same-named classes that agree on the key collapse to one — agreement is the answer.
    Disagreement is returned whole so the consumer's row can say how many, because
    naming one of them would be a guess wearing provenance.
    """
    field = field_of_getter(getter)
    hits = []
    for bindings, gettable in registry.get(strip_generics(receiver_type), []):
        if field in bindings and field in gettable:
            hits.append(bindings[field])
    if hits and len({b.raw for b in hits}) == 1:
        return hits[:1]
    return hits
