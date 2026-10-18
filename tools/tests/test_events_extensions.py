"""Z1: declared extension fields on events, for frameworks built on the harness.

A framework that drives the harness (e.g. agentic-sdlc) needs a few more ids on
each event - which team, which agent and version, which tier, which policy
version. They go in an optional `ext` block that:

* is declared first (`register_extension`): an undeclared namespace or field is
  refused exactly like an undeclared core field;
* carries no free text: values are ids, names, versions or hashes (the module's
  own rule), so a note or a secret cannot ride along;
* is covered by the chain hash: editing it breaks `verify` like any other edit;
* is optional: an event written without it is byte-for-byte what it was before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import schema as S  # noqa: E402
from tools.sdlc import state as L  # noqa: E402

RUN = "r-ext-1"
AGENTIC = {"team": "string", "agent": "string", "tier": "string",
           "policy_version": "string", "skills": "string[]"}


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(E.EXTENSIONS)
    E.EXTENSIONS.clear()
    yield
    E.EXTENSIONS.clear()
    E.EXTENSIONS.update(saved)


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (L.runs_dir(tmp_path) / RUN).mkdir(parents=True)
    return tmp_path


def opened(target: Path, **kwargs) -> dict:
    return E.append(target, RUN, E.RUN_OPENED, kind="feature", project="p",
                    owner="o@example.invalid", criticality="low", **kwargs)


def test_registered_extension_is_written_and_chained(target):
    E.register_extension("agentic", AGENTIC)

    entry = opened(target, ext={"agentic": {"team": "payments", "agent": "developer@1.2.0",
                                            "tier": "balanced", "skills": ["superpowers/tdd"]}})

    assert entry["ext"]["agentic"]["agent"] == "developer@1.2.0"
    assert E.verify(target, RUN) == []
    assert E.read(target, RUN)[0]["ext"] == entry["ext"]


def test_editing_an_extension_value_breaks_the_chain(target):
    E.register_extension("agentic", AGENTIC)
    opened(target, ext={"agentic": {"team": "payments"}})
    path = E.events_path(target, RUN)
    row = json.loads(path.read_text(encoding="utf-8"))
    row["ext"]["agentic"]["team"] = "platform"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    assert any("hash does not match" in p for p in E.verify(target, RUN))


def test_event_without_ext_is_unchanged(target):
    entry = opened(target)

    assert "ext" not in entry


def test_unregistered_namespace_is_refused(target):
    with pytest.raises(E.EventError, match="not a registered extension"):
        opened(target, ext={"mystery": {"x": "y"}})


def test_undeclared_extension_field_is_refused(target):
    E.register_extension("agentic", AGENTIC)

    with pytest.raises(E.EventError, match="mood"):
        opened(target, ext={"agentic": {"mood": "happy"}})


def test_extension_values_carry_no_free_text(target):
    E.register_extension("agentic", AGENTIC)

    with pytest.raises(E.EventError, match="not an id"):
        opened(target, ext={"agentic": {"team": "the note says sk-live-xyz and a customer"}})


def test_extension_value_types_are_checked(target):
    E.register_extension("agentic", AGENTIC)

    with pytest.raises(E.EventError, match="team"):
        opened(target, ext={"agentic": {"team": 5}})
    with pytest.raises(E.EventError, match="skills"):
        opened(target, ext={"agentic": {"skills": "not-a-list"}})


def test_refused_extension_writes_nothing(target):
    E.register_extension("agentic", AGENTIC)

    with pytest.raises(E.EventError):
        opened(target, ext={"agentic": {"team": "has spaces"}})

    assert E.read(target, RUN) == []


def test_reregistering_the_same_fields_is_fine_but_changing_them_is_refused():
    E.register_extension("agentic", AGENTIC)
    E.register_extension("agentic", dict(AGENTIC))  # idempotent

    with pytest.raises(E.EventError, match="already registered"):
        E.register_extension("agentic", {"team": "integer"})


@pytest.mark.parametrize("bad", [{"Bad Name": {}}, {"agentic": {"Bad-Field": "string"}},
                                 {"agentic": {"team": "date"}}])
def test_registration_is_validated(bad):
    (namespace, fields), = bad.items()
    with pytest.raises(E.EventError):
        E.register_extension(namespace, fields or {"x": "string"})


def test_events_schema_accepts_ext_on_every_event(target):
    E.register_extension("agentic", AGENTIC)
    entry = opened(target, ext={"agentic": {"team": "payments"}})

    jsonschema.validate(entry, S.events_schema())  # ext is an allowed key
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**entry, "surprise": 1}, S.events_schema())
