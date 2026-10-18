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


# ── review fixes (PR #78) ─────────────────────────────────────────────────────

import math  # noqa: E402
import subprocess  # noqa: E402

from tools.sdlc import metrics as M  # noqa: E402


@pytest.fixture(autouse=True)
def clean_default_provider():
    yield
    E.set_default_ext(None)


def test_numbers_must_be_finite_and_bounded(target):
    E.register_extension("m", {"n": "number", "i": "integer"})

    for bad in (float("nan"), float("inf"), -math.inf, 10**40):
        with pytest.raises(E.EventError, match="m.n"):
            opened(target, ext={"m": {"n": bad}})
    with pytest.raises(E.EventError, match="m.i"):
        opened(target, ext={"m": {"i": 2**60}})
    with pytest.raises(E.EventError, match="m.i"):
        opened(target, ext={"m": {"i": True}})  # a bool is not an integer
    assert opened(target, ext={"m": {"n": 1.5, "i": -7}})["ext"] == {"m": {"n": 1.5, "i": -7}}


def test_boolean_and_list_types(target):
    E.register_extension("m", {"flag": "boolean", "skills": "string[]"})

    with pytest.raises(E.EventError, match="m.flag"):
        opened(target, ext={"m": {"flag": 1}})
    with pytest.raises(E.EventError, match="m.skills"):
        opened(target, ext={"m": {"skills": [f"s{i}" for i in range(101)]}})
    with pytest.raises(E.EventError, match="not an id"):
        opened(target, ext={"m": {"skills": ["ok", "has space"]}})


@pytest.mark.parametrize("value", ["abc\n", "abc\r", "x" * 129])
def test_values_with_a_trailing_newline_or_too_long_are_refused(target, value):
    E.register_extension("agentic", AGENTIC)

    with pytest.raises(E.EventError, match="not an id"):
        opened(target, ext={"agentic": {"team": value}})


@pytest.mark.parametrize("namespace, field", [("abc\n", "team"), ("agentic", "team\n")])
def test_names_with_a_trailing_newline_are_refused(namespace, field):
    with pytest.raises(E.EventError):
        E.register_extension(namespace, {field: "string"})


def test_shape_alone_does_not_stop_a_space_free_secret_so_fields_can_be_constrained(target):
    # The id shape keeps out prose; it cannot recognise a key that has no spaces.
    # That is why a field can carry its own pattern or allowed values.
    key_shaped = "sk-ant-api03-AbCdEf1234567890AbCdEf1234567890"
    E.register_extension("loose", {"team": "string"})
    assert opened(target, ext={"loose": {"team": key_shaped}})["ext"]["loose"]["team"] == key_shaped

    E.register_extension("agentic", {
        "tier": {"type": "string", "enum": ["fast", "balanced", "deep-reasoning"]},
        "policy_version": {"type": "string",
                           "pattern": r"(git:[0-9a-f]{40}|sha256:[0-9a-f]{64})"},
    })
    with pytest.raises(E.EventError, match="agentic.policy_version"):
        opened(target, ext={"agentic": {"policy_version": key_shaped}})
    with pytest.raises(E.EventError, match="agentic.tier"):
        opened(target, ext={"agentic": {"tier": key_shaped}})
    ok = opened(target, ext={"agentic": {"tier": "balanced", "policy_version": "git:" + "a" * 40}})
    assert ok["ext"]["agentic"]["tier"] == "balanced"


@pytest.mark.parametrize("spec", [{"type": "string", "pattern": "("}, {"type": "date"},
                                  {"type": "string", "enum": "fast"}, {"pattern": "x"}, None])
def test_field_constraints_are_validated_at_registration(spec):
    with pytest.raises(E.EventError):
        E.register_extension("agentic", {"tier": spec})


def test_registering_none_is_an_event_error():
    with pytest.raises(E.EventError):
        E.register_extension("agentic", None)


def test_ext_may_ride_on_every_event_type(target):
    E.register_extension("agentic", AGENTIC)

    for event in E.TYPES:
        entry = E.append(target, RUN, event, ext={"agentic": {"team": "payments"}})
        assert entry["ext"] == {"agentic": {"team": "payments"}}
    assert E.verify(target, RUN) == []


def test_an_event_without_ext_has_exactly_the_keys_it_had_before(target):
    entry = opened(target)

    assert set(entry) == {*E.COMMON, "kind", "project", "owner", "criticality"}


def test_a_mixed_chain_verifies(target):
    E.register_extension("agentic", AGENTIC)
    opened(target)
    E.append(target, RUN, E.STEP_STARTED, attempt=1, actor="agent",
             ext={"agentic": {"agent": "developer@1.2.0"}})
    E.append(target, RUN, E.RUN_CLOSED, status="done")

    assert E.verify(target, RUN) == []


def test_empty_ext_writes_no_key_and_non_mapping_ext_is_refused(target):
    assert "ext" not in opened(target, ext={})
    for bad in ([], "", 0):
        with pytest.raises(E.EventError, match="mapping"):
            opened(target, ext=bad)


def test_changing_the_callers_dict_after_append_changes_nothing(target):
    E.register_extension("agentic", AGENTIC)
    mine = {"agentic": {"team": "payments"}}
    entry = opened(target, ext=mine)
    mine["agentic"]["team"] = "platform"

    assert entry["ext"]["agentic"]["team"] == "payments"
    assert E.read(target, RUN)[0]["ext"]["agentic"]["team"] == "payments"


def test_an_oversized_ext_block_is_refused(target):
    E.register_extension("m", {f"f{i}": "string[]" for i in range(10)})

    with pytest.raises(E.EventError, match="bytes"):
        opened(target, ext={"m": {f"f{i}": ["x" * 120] * 100 for i in range(10)}})


# ── default ext: events the harness writes itself carry the framework's ids ──


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text("gate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    return r


def test_default_ext_reaches_events_written_by_the_harness_itself(repo):
    E.register_extension("agentic", AGENTIC)
    E.set_default_ext(lambda event, run_id: {"agentic": {"team": "payments"}})

    rid = L.create(repo, "requirement", "a change")["run_id"]  # the harness writes run_opened

    assert all(e["ext"] == {"agentic": {"team": "payments"}} for e in E.read(repo, rid))
    assert E.verify(repo, rid) == []


def test_explicit_ext_overrides_the_default_field_by_field(target):
    E.register_extension("agentic", AGENTIC)
    E.set_default_ext(lambda event, run_id: {"agentic": {"team": "payments", "tier": "fast"}})

    entry = opened(target, ext={"agentic": {"tier": "balanced"}})

    assert entry["ext"] == {"agentic": {"team": "payments", "tier": "balanced"}}


def test_an_invalid_default_ext_refuses_the_event_and_writes_nothing(target):
    E.register_extension("agentic", AGENTIC)
    E.set_default_ext(lambda event, run_id: {"agentic": {"team": "has spaces"}})

    with pytest.raises(E.EventError):
        opened(target)
    assert E.read(target, RUN) == []


def test_clearing_the_default_restores_plain_events(target):
    E.register_extension("agentic", AGENTIC)
    E.set_default_ext(lambda event, run_id: {"agentic": {"team": "payments"}})
    E.set_default_ext(None)

    assert "ext" not in opened(target)


# ── where ext surfaces ─────────────────────────────────────────────────────────


def test_ext_is_kept_in_the_ndjson_export(repo, tmp_path):
    E.register_extension("agentic", AGENTIC)
    E.set_default_ext(lambda event, run_id: {"agentic": {"team": "payments"}})
    rid = L.create(repo, "requirement", "a change")["run_id"]

    files = M.export(repo, "ndjson", tmp_path / "out")
    rows = [json.loads(line) for f in files if f.suffix == ".ndjson"
            for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    opened_rows = [r for r in rows if r.get("event") == "run_opened" and r.get("run_id") == rid]

    assert opened_rows and opened_rows[0]["ext"] == {"agentic": {"team": "payments"}}
