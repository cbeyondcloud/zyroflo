"""Shared YAML reading helpers for the stack extractors that need them.

Underscore-prefixed, so the extractor registry skips it — the same arrangement as `_java/`.
This is shared *code*, not a shared cache entry: there is no state here and nothing to keep
in step, only one implementation of a thing two readers were each getting wrong separately.

## Why a YAML-aware line finder exists at all

`props.find_line` is a substring search. That is right for a properties file, where a key is
the start of a line, and **wrong for YAML**, where a key's name routinely appears inside an
earlier value:

    services:
      api:
        environment:
          DSN: postgresql://db:5432/app     <- `db:` matches HERE
      db:                                   <- and the service is HERE
        image: postgres:16

The second fixture caught this in the compose reader, which reported a service eleven lines
before it was declared. The helm reader had the identical defect in its configmap keys and
kept it for another item, because a defect fixed outside the item that found it arrives
without a regression test.

**This is the shape of wrongness that survives review.** The number is plausible, the edge is
otherwise correct, and only someone opening the file and counting would ever know. Nothing
downstream can detect it, which is why the answer keys assert line numbers at all.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

from tools.graph.core.lib import props


def key_line(text: str, key: str, start: int = 0) -> tuple[int, int]:
    """The 1-based line where `key:` is *declared*, plus an offset to resume scanning from.

    Anchored to the start of a line, after indentation and an optional list dash — which is
    what makes it a key rather than a coincidence inside a value.

    The returned offset lets a caller keep searching **forward**, so a key repeated under two
    blocks resolves to its own occurrence rather than to the first one for both. A caller that
    ignores it gets the first match, which is correct for a single lookup and wrong in a loop.

    Returns `(0, start)` when the key is not found: line 0 is the "no line" convention already
    used across the extractors, and the unchanged offset keeps a scanning loop from advancing
    on a miss.
    """
    m = re.compile(rf"^[ \t]*-?[ \t]*{re.escape(key)}[ \t]*:", re.M).search(text, start)
    if m is None:
        return 0, start
    return text.count("\n", 0, m.start()) + 1, m.end()


#: What a Go-template span becomes so the YAML around it can parse. Distinctive enough
#: that no real configuration value contains it, so a reader can refuse to believe any
#: value it appears in.
TOKEN = "__TPL__"

_SPAN = re.compile(r"\{\{.*?\}\}")


def detemplate(text: str) -> str:
    """Go-templated YAML → parseable YAML, with the LINE COUNT untouched.

    A chart's `templates/*.yaml` are YAML skeletons carrying `{{ ... }}` spans in scalar
    positions and whole-line control constructs between mappings. Rendering them needs a
    template engine, a values context and a release name — none of which a static reader
    has — but parsing them only needs the spans out of the way:

    * an inline span becomes `TOKEN`, so `name: {{ template "x" . }}` parses and the
      reader can see the value is not a literal;
    * a line that is ONLY template machinery (`{{- if }}`, `{{- end }}`, a span left
      dangling across lines) becomes a comment of itself;

    and every substitution happens WITHIN its line, never across one — the whole point
    is that `key_line` citations against the original text stay true.
    """
    out = []
    in_span = False
    for line in text.splitlines():
        if in_span:
            out.append("# " + line)
            in_span = "}}" not in line
            continue
        replaced = _SPAN.sub(TOKEN, line)
        if "{{" in replaced:                      # a span opened and never closed here
            out.append("# " + line)
            in_span = True
        elif replaced.strip().startswith(TOKEN) and not replaced.strip()[len(TOKEN):].strip(" -"):
            out.append("# " + line)               # the line IS the construct
        else:
            out.append(replaced)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


# ── Reading values from the node tree ─────────────────────────────────────────
#
# `safe_load` hands back what YAML TYPES a value as, and the readers recorded that: an
# unquoted `1.10` became `1.1`, `yes` became `True` and `off` became `False` - values no
# file contains (T536). And a key merged in through an anchor (`<<: *env`) has no position
# in the loaded dict, so a forward text scan for it found the next service's key of the same
# name, which declares a different value (T539). PyYAML's node tree, the parse `safe_load`
# builds its values from, keeps both: each scalar's text as written, and each key's own
# position - an anchored key's is the line that declares it.

#: A YAML merge key (`<<: *base`): the mapping it names joins this one.
MERGE_TAG = "tag:yaml.org,2002:merge"
#: A scalar YAML reads as text - a quoted one always, a plain one it types as nothing else.
STR_TAG = "tag:yaml.org,2002:str"
_NULL_TAG = "tag:yaml.org,2002:null"
_TYPED_TAGS = ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float")


def document_nodes(text: str) -> list[Any]:
    """Every document in `text` as a node tree - the parse `safe_load_all` builds its values
    from, stopped before a value is typed."""
    return list(props.yaml.compose_all(text, Loader=props.yaml.SafeLoader))


def mapping_pairs(node: Any, _expanding: frozenset = frozenset()) -> list[tuple[Any, Any]]:
    """A mapping node's `(key node, value node)` pairs, merge keys expanded as YAML defines
    them and in the order `safe_load` builds the dict in: merged keys first, a key the
    mapping writes itself winning over one it merges in, and among several merged mappings
    the first listed winning. [] for anything that is not a mapping.

    Each pair keeps its own nodes, so a merged key is cited at the line that declares it.

    A mapping that merges itself (`api: &api` holding `<<: *api`), directly or through
    another, is valid YAML that `safe_load` reads: PyYAML drops a merge before following
    it, so a mapping met again mid-expansion adds its own written pairs and nothing more.
    The same here - followed, it recursed until the interpreter stopped it and cost the
    whole extractor its workspace (review B10-1). The keys and values are then PyYAML's;
    the ORDER is too, except in a mapping that merges itself AND carries other merge keys,
    where PyYAML's in-place deletion folds some of them in first (review B10-11) - which
    moves the order of what is emitted for such a file, never what it says.

    Two keys are one when YAML reads them as one: the same text, typed the same way. So
    `a:` and `"a":` fold, and `1:` and `"1":` - an int and a string - do not (review
    B10-14). Keys YAML TYPES alike but writes differently (`on:`, `true:`) stay two, as
    written: which is right is the reading tool's to say, and one lost is a drop."""
    y = props.yaml
    if not isinstance(node, y.MappingNode):
        return []
    expanding = _expanding | {id(node)}
    merged: list[tuple[Any, Any]] = []
    written: list[tuple[Any, Any]] = []
    for key, value in node.value:
        if key.tag != MERGE_TAG:
            written.append((key, value))
            continue
        sources = ([value] if isinstance(value, y.MappingNode) else
                   [s for s in value.value if isinstance(s, y.MappingNode)]
                   if isinstance(value, y.SequenceNode) else [])
        for source in reversed(sources):      # PyYAML's order: the first listed lands last
            if id(source) in expanding:
                merged.extend((k, v) for k, v in source.value if k.tag != MERGE_TAG)
            else:
                merged.extend(mapping_pairs(source, expanding))
    folded: dict[tuple[str, str], tuple[Any, Any]] = {}
    for key, value in merged + written:
        # The later one wins; the first keeps its place.
        folded[(key.tag, str(key.value))] = (key, value)
    return list(folded.values())


def mapping_get(node: Any, key: str) -> Any:
    """The value node `key` names in a mapping node, merges expanded; None when absent."""
    return next((v for k, v in mapping_pairs(node) if str(k.value) == key), None)


def line_of(node: Any) -> int:
    """The 1-based line a node starts on."""
    return node.start_mark.line + 1


def constructed(node: Any) -> Any:
    """What `safe_load` builds from a node - for a shape read no other way, such as a
    mapping where a scalar belongs. A copy is built, because construction flattens merge
    keys in place and an anchor's node is shared."""
    return props.yaml.SafeLoader("").construct_document(copy.deepcopy(node))


def scalar_text(node: Any) -> str:
    """A scalar as it was WRITTEN, never as YAML types it: a quoted scalar's content, a plain
    one's text. A plain scalar YAML reads as null (`~`, `null`, nothing at all) states no
    value, so it is ""."""
    if node.style is None and node.tag == _NULL_TAG:
        return ""
    return node.value


#: YAML 1.2's core schema, for the plain scalars it types unlike YAML 1.1, the schema PyYAML
#: resolves: `1e3` and `0o17` are numbers in 1.2 and text in 1.1; `yes`, `010` and `1_000`
#: the other way round. A file may reach a reader of either, so both are asked.
_CORE_NULL = {"", "~", "null", "Null", "NULL"}
_CORE_BOOL = re.compile(r"true|True|TRUE|false|False|FALSE")
_CORE_INT = re.compile(r"[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+")
_CORE_FLOAT = re.compile(r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?")
_CORE_INF = re.compile(r"([-+]?)\.(?:inf|Inf|INF)")
_CORE_NAN = re.compile(r"\.(?:nan|NaN|NAN)")


def _core_value(text: str) -> Any:
    """What YAML 1.2's core schema types a plain scalar as - None for null, the text itself
    for a string."""
    if text in _CORE_NULL:
        return None
    if _CORE_BOOL.fullmatch(text):
        return text.lower() == "true"
    if _CORE_INT.fullmatch(text):
        base = {"0o": 8, "0x": 16}.get(text[:2], 10)
        return int(text[2:] if base != 10 else text, base)
    if _CORE_FLOAT.fullmatch(text):
        return float(text)
    if m := _CORE_INF.fullmatch(text):
        return -math.inf if m.group(1) == "-" else math.inf
    if _CORE_NAN.fullmatch(text):
        return math.nan
    return text


def _written_back(value: Any) -> set[str]:
    """How a reader that typed a value may write it back out: a bool in lower case, an int
    in decimal, a float as its shortest spelling and, when whole, without its fraction -
    readers disagree about `2.0`. Text, null and dates keep their text, so none."""
    if isinstance(value, bool):
        return {"true" if value else "false"}
    if isinstance(value, int):
        return {str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {".nan"}                    # YAML's own spelling, not Python's (B10-6)
        if math.isinf(value):
            return {".inf" if value > 0 else "-.inf"}
        return {repr(value)} | ({str(int(value))} if value.is_integer() else set())
    return set()


#: YAML 1.1 as its specification writes it counts `y` and `n` among the booleans, which
#: PyYAML's resolver leaves out and other 1.1 readers do not (review B10-5).
_SPEC_11_BOOLS = {"y": True, "Y": True, "n": False, "N": False}


def typed_readings(node: Any) -> tuple[str, ...]:
    """What a YAML reader may deliver a plain scalar as, other than as written - sorted, and
    () when every reader keeps its text (`8080`, `true`, `1.5`).

    `1.10` may arrive as `1.1`, `yes` as `true`, `010` as `8` or `10`, and which one a
    service gets depends on the tool that reads the file, which a static reader cannot
    know. So the value is recorded as written and this says what else it may be. A quoted
    scalar, and one tagged explicitly (`!!str 1.10`), is what its author said it is."""
    y = props.yaml
    if not isinstance(node, y.ScalarNode) or node.style is not None:
        return ()
    text = node.value
    if node.tag != y.resolver.Resolver().resolve(y.ScalarNode, text, (True, False)):
        return ()                                  # an explicit tag decides it
    readings = _written_back(_core_value(text))
    if node.tag in _TYPED_TAGS:
        readings |= _written_back(constructed(node))
    if text in _SPEC_11_BOOLS:
        readings |= _written_back(_SPEC_11_BOOLS[text])
    readings.discard(text)
    return tuple(sorted(readings))


def may_agree(a: str, a_readings: tuple[str, ...], b: str, b_readings: tuple[str, ...]) -> bool:
    """Whether two values written differently may still be one value to the tool that
    reads them - `yes` and `true` are to a YAML 1.1 reader - because a reading of one is
    the other, or both read the same way."""
    return bool({a, *a_readings} & {b, *b_readings})


def typed_value_why(key: str, text: str, readings: tuple[str, ...]) -> str:
    """The `why` of a `yaml-typed-value` row, the same sentence from every reader that emits
    one."""
    said = " or ".join(f"`{r}`" for r in readings)
    return (f"`{key}: {text}` is written unquoted, and a YAML reader types it, so a service "
            f"may receive {said} rather than `{text}` - which one depends on the tool that "
            f"reads this file. The graph records it as written; quoting it makes it certain")
