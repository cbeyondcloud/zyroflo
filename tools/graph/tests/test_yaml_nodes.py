"""The node-tree readers in `extractors/_yaml` (T536, T539).

`safe_load` types a value - `1.10` becomes `1.1`, `off` becomes `False` - and drops where a
merged key was declared. The readers here keep both, and these tests hold them to two
authorities: PyYAML itself for what a document MEANS (the keys, their order and which value
wins), and a table for how a scalar may be read other than as written.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import props  # noqa: E402
from tools.graph.extractors import _yaml  # noqa: E402

pytestmark = pytest.mark.skipif(not props.yaml_available(),
                                reason="no parser installed for this language")


def _value(text: str):
    """The node of `key: <text>`'s value."""
    (doc,) = _yaml.document_nodes(f"key: {text}\n")
    return _yaml.mapping_get(doc, "key")


@pytest.mark.parametrize("text, readings", [
    ("1.10", ("1.1",)),            # a float to every reader, which drops the zero
    ("yes", ("true",)),            # a bool in YAML 1.1, text in 1.2
    ("off", ("false",)),
    ("True", ("true",)),           # a bool to both, written back in lower case
    ("010", ("10", "8")),          # octal in 1.1, decimal in 1.2
    ("2.0", ("2",)),               # whole: some readers drop the fraction
    ("1e3", ("1000", "1000.0")),   # text in 1.1, a float in 1.2
    ("0o17", ("15",)),             # text in 1.1, octal in 1.2
    ("1_000", ("1000",)),          # an int in 1.1, text in 1.2
    ("true", ()),                  # the same to every reader
    ("8080", ()),
    ("1.5", ()),
    ("orders-v2", ()),
    ("~", ()),                     # null: no value, and nothing else to be
    ("2024-01-01", ()),            # a date keeps its text
    ('"1.10"', ()),                # quoted: text, as its author said
    ("!!str 1.10", ()),            # tagged: the same
])
def test_what_a_reader_may_deliver_a_scalar_as(text: str, readings: tuple):
    assert _yaml.typed_readings(_value(text)) == readings


@pytest.mark.parametrize("text, written", [
    ("1.10", "1.10"), ("off", "off"), ('"1.10"', "1.10"), ("'it''s'", "it's"),
    ("~", ""), ("null", ""), ("", ""), ("!!str 1.10", "1.10"),
])
def test_a_scalar_is_read_as_it_was_written(text: str, written: str):
    assert _yaml.scalar_text(_value(text)) == written


MERGES = [
    "a: &a {x: 1, y: 2}\nb:\n  <<: *a\n  y: 3\n  z: 4\n",
    "a: &a {x: 1}\nc: &c {x: 2, w: 5}\nb:\n  <<: [*a, *c]\n  z: 4\n",
    "a: &a {x: 1}\nb:\n  z: 4\n  <<: *a\n",
    "a: &a {x: 1}\nd: &d {<<: *a, y: 2}\nb:\n  <<: *d\n  y: 3\n",
    "b: {k: v, j: w}\n",
]


@pytest.mark.parametrize("text", MERGES)
def test_the_pairs_mean_what_safe_load_means(text: str):
    """Merge keys expanded exactly as PyYAML expands them - the same keys, in the same
    order, the same value winning - checked against PyYAML rather than restated."""
    (doc,) = _yaml.document_nodes(text)
    pairs = _yaml.mapping_pairs(_yaml.mapping_get(doc, "b"))
    built = {k.value: _yaml.constructed(v) for k, v in pairs}
    loaded = props.yaml.safe_load(text)["b"]
    assert list(built.items()) == list(loaded.items())


def test_a_merged_key_keeps_the_line_that_declares_it():
    text = "a: &a\n  x: 1\nb:\n  <<: *a\n  y: 2\n"
    (doc,) = _yaml.document_nodes(text)
    lines = {k.value: _yaml.line_of(k) for k, _ in _yaml.mapping_pairs(_yaml.mapping_get(doc, "b"))}
    assert lines == {"x": 2, "y": 5}


def test_reading_a_shared_anchor_does_not_change_it():
    """`constructed` builds a copy: construction flattens merge keys in place, and an
    anchor's node is shared by every alias to it."""
    text = "a: &a {x: 1}\nb: &b {<<: *a, y: 2}\nc: *b\n"
    (doc,) = _yaml.document_nodes(text)
    before = [k.value for k, _ in _yaml.mapping_get(doc, "b").value]
    _yaml.constructed(_yaml.mapping_get(doc, "c"))
    assert [k.value for k, _ in _yaml.mapping_get(doc, "b").value] == before
