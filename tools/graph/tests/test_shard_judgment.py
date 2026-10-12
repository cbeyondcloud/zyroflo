"""Batch 16 (T627 to T630; the owner's rulings 27-30): the agreement matrix.

Every shard state the design note lists goes through all three judging paths, each spawned as
a process - `--check-fresh`, `--incremental` and a `--from-snapshot` load - and they must name
the same thing:

* a current shard is fresh, reused, and loaded into an un-narrowed run;
* a stale one is STALE, rebuilt in the plan's words, and loaded into a narrowed run;
* a refused one is REFUSED, rebuilt with its reason, and refused by the load into
  `snapshot.mismatched`;
* a missing one is STALE, rebuilt, and missing from the load.

In every state `--incremental` equals a full build byte for byte - a hand edit whose digest
was recomputed aside, which is `--check-snapshot`'s to find. Each state's remedy, followed, is
accepted: `--check-fresh` exits 0, every component is fresh and the load is un-narrowed. A
nesting cell states its depth in its name (D16-46).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import snapshot as SN  # noqa: E402
from tools.graph.core.lib import props, srcfacts  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, producer, svc  # noqa: E402

#: `a`'s compose file answers `b`'s producer through the shared cache.
FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
#: A spec only `openapi-specs` reads, with YAML - a reader that is no deployment reader.
SPEC_ESTATE = {"a": {"openapi.yaml": "openapi: 3.0.0\ninfo:\n  title: t\n  version: '1'\n"
                                     "paths: {}\n"},
               "b": {"app.py": "print('b')\n"}}
#: A test file `tests` parses with the Python grammar.
TESTS_ESTATE = {"a": {"tests/test_app.py": "def test_x():\n    assert 'http://svc/x'\n"},
                "b": {"app.py": "print('b')\n"}}
MISSING = "no freshness stamps — no committed shard, or one published before stamps existed"
OWN_FILES = ("files read by ", "files no extractor reads", "a file a deployment reader")


# ── the parser state, and an OS that will not read a manifest ──────────────────────────────


@contextmanager
def parsers(no_yaml: bool = False, no_python: bool = False):
    """This process's parser state, as a tree without PyYAML or the Python grammar reads."""
    saved = props.yaml_available, srcfacts.available
    real = srcfacts.available
    if no_yaml:
        props.yaml_available = lambda: False
    if no_python:
        srcfacts.available = lambda name: name != "python" and real(name)
    try:
        yield
    finally:
        props.yaml_available, srcfacts.available = saved


def preamble(no_yaml: bool = False, no_python: bool = False, os_refuses: bool = False) -> str:
    """The same state, arranged in a child before it imports the build."""
    lines = []
    if no_yaml:
        lines += ["from tools.graph.core.lib import props",
                  "props.yaml_available = lambda: False"]
    if no_python:
        lines += ["from tools.graph.core.lib import srcfacts",
                  "_available = srcfacts.available",
                  "srcfacts.available = lambda name: name != 'python' and _available(name)"]
    if os_refuses:
        lines += ["import pathlib",
                  "_read = pathlib.Path.read_bytes",
                  "def _refusing(self):",
                  "    if self.name == 'snapshot.json' and self.parent.parent.parent.name == 'a':",
                  "        raise PermissionError(13, 'Permission denied')",
                  "    return _read(self)",
                  "pathlib.Path.read_bytes = _refusing"]
    return "".join(f"{line}\n" for line in lines)


def spawn(where: Path, flag: str, out: str, pre: str = "") -> subprocess.CompletedProcess:
    runner = where / f"run_{out}.py"
    runner.write_text(f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n{pre}"
                      f"import tools.graph.build as B\nraise SystemExit(B.main(sys.argv[1:]))\n",
                      encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(runner), "--workspace", str(where / "ws" / "workspace.toml"),
         "--out", str(where / out), "--run-id", "fixed", "--empty-ok", flag],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def main(where: Path, out: str, *flags: str) -> int:
    return B.main(["--workspace", str(where / "ws" / "workspace.toml"), "--out",
                   str(where / out), "--run-id", "fixed", "--empty-ok", *flags])


def publish(where: Path, **state) -> None:
    with parsers(**state):
        assert main(where, "pub", "--publish-snapshot") == 0


def refusing(read: Callable[[Path], bytes]) -> Callable[[Path], bytes]:
    """`Path.read_bytes`, refusing `a`'s manifest as an OS that will not read it does."""
    def read_bytes(self: Path) -> bytes:
        if self.name == SN.MANIFEST and self.parent.parent.parent.name == "a":
            raise PermissionError(13, "Permission denied")
        return read(self)
    return read_bytes


def report(where: Path, out: str) -> dict:
    return json.loads((where / out / "build_report.json").read_text(encoding="utf-8"))


# ── the damage ─────────────────────────────────────────────────────────────────────────────


def gdir(where: Path, comp: str = "a") -> Path:
    return where / "ws" / comp / ".sdlc" / "graph"


def edit_manifest(where: Path, edit: Callable[[dict], object]) -> None:
    path = gdir(where) / SN.MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")


def restamp(where: Path) -> None:
    edit_manifest(where, lambda m: m.update(sha256=SN._digest(gdir(where))))


def append_facts(line: bytes) -> Callable[[Path], None]:
    def damage(where: Path) -> None:
        shard = gdir(where) / SN.SHARD
        shard.write_bytes(shard.read_bytes() + line + b"\n")
        restamp(where)
    return damage


def set_rows(raw: bytes) -> Callable[[Path], None]:
    def damage(where: Path) -> None:
        (gdir(where) / SN.SHARD_UNRESOLVED).write_bytes(raw)
        restamp(where)
    return damage


def manifest_bytes(raw: bytes) -> Callable[[Path], None]:
    return lambda where: (gdir(where) / SN.MANIFEST).write_bytes(raw)


def a_directory_at(name: str) -> Callable[[Path], None]:
    def damage(where: Path) -> None:
        (gdir(where) / name).unlink()
        (gdir(where) / name).mkdir()
    return damage


def remove_directory_at(name: str) -> Callable[[Path], None]:
    return lambda where: (gdir(where) / name).rmdir()


def edit_file(comp: str, rel: str, body: str) -> Callable[[Path], None]:
    return lambda where: (where / "ws" / comp / rel).write_text(body, encoding="utf-8")


def no_stamps(where: Path) -> None:
    """A shard published before stamps existed: a digest and nothing else."""
    edit_manifest(where, lambda m: [m.pop(k) for k in list(m) if k not in ("sha256",)])
    edit_manifest(where, lambda m: m.update(schema_version=1))


def missing_facts(where: Path) -> None:
    (gdir(where) / SN.SHARD).unlink()
    restamp(where)


def tamper(where: Path) -> None:
    shard = gdir(where) / SN.SHARD
    shard.write_bytes(shard.read_bytes() + b"\n")


def mixed_publish(where: Path) -> None:
    """D16-1: `a` published without PyYAML, `b` by another publish with it."""
    kept = where / "kept-b"
    shutil.copytree(gdir(where, "b"), kept)
    publish(where, no_yaml=True)
    shutil.rmtree(gdir(where, "b"))
    shutil.copytree(kept, gdir(where, "b"))


def emptied_sibling(where: Path) -> None:
    """D16-52: `a` gives `b`'s readers nothing any more, re-published; `b`'s shard is older."""
    kept = where / "kept-b"
    shutil.copytree(gdir(where, "b"), kept)
    (where / "ws" / "a" / "docker-compose.yml").unlink()
    publish(where)
    shutil.rmtree(gdir(where, "b"))
    shutil.copytree(kept, gdir(where, "b"))


@dataclass(frozen=True)
class State:
    id: str
    expect: dict[str, str]
    damage: Callable[[Path], None] | None = None
    estate: dict = field(default_factory=lambda: FEED)
    seed: dict = field(default_factory=dict)
    now: dict = field(default_factory=dict)
    os_refuses: bool = False
    remedy: Callable[[Path], None] | None = None
    unparsed: dict = field(default_factory=dict)


REFUSED_A = {"a": "refused", "b": "stale"}     # its stamps unreadable: `b` cannot compare
REFUSED_A_ALONE = {"a": "refused", "b": "current"}
STATES = [
    State("current", {"a": "current", "b": "current"}),
    State("own-inputs-changed", {"a": "current", "b": "stale"},
          damage=edit_file("b", "app.py", producer("OTHER"))),
    State("inputs-changed-elsewhere", {"a": "stale", "b": "stale"},
          damage=edit_file("a", "docker-compose.yml", compose(svc("b", "../b", "web-2")))),
    State("no-shard", {"a": "missing", "b": "stale"},
          damage=lambda where: shutil.rmtree(gdir(where))),
    State("no-stamps", {"a": "stale", "b": "stale"}, damage=no_stamps),
    State("no-parser-stamp", {"a": "stale", "b": "current"},
          damage=lambda where: edit_manifest(where, lambda m: m.pop("parsers"))),
    State("manifest-a-directory", REFUSED_A, damage=a_directory_at(SN.MANIFEST),
          remedy=remove_directory_at(SN.MANIFEST)),
    State("manifest-the-os-will-not-read", REFUSED_A, os_refuses=True),
    State("manifest-not-json", REFUSED_A, damage=manifest_bytes(b"{not json")),
    State("manifest-not-utf8", REFUSED_A, damage=manifest_bytes(b'{"sha256": "\xff"}')),
    State("manifest-nested-4000-deep", REFUSED_A,
          damage=manifest_bytes(b"[" * 4000 + b"]" * 4000)),
    State("manifest-no-sha256", REFUSED_A,
          damage=lambda where: edit_manifest(where, lambda m: m.pop("sha256"))),
    State("manifest-wrong-stamp-types", REFUSED_A,
          damage=lambda where: edit_manifest(where, lambda m: m.update(inputs="x"))),
    State("shard-file-unreadable", REFUSED_A_ALONE, damage=a_directory_at(SN.SHARD),
          remedy=remove_directory_at(SN.SHARD)),
    State("facts-file-missing-digest-recomputed", REFUSED_A_ALONE, damage=missing_facts),
    State("bytes-tampered", REFUSED_A_ALONE, damage=tamper),
    State("facts-not-json", REFUSED_A_ALONE, damage=append_facts(b"not json")),
    State("facts-nested-4000-deep", REFUSED_A_ALONE,
          damage=append_facts(b"[" * 4000 + b"]" * 4000)),
    State("facts-a-list", REFUSED_A_ALONE, damage=append_facts(b'["a list"]')),
    State("facts-a-string", REFUSED_A_ALONE, damage=append_facts(b'"text"')),
    State("facts-null", REFUSED_A_ALONE, damage=append_facts(b"null")),
    State("facts-not-utf8", REFUSED_A_ALONE,
          damage=append_facts(b'{"kind": "node", "id": "svc:\xff", "type": "svc", "props": {}}')),
    State("rows-not-json", REFUSED_A_ALONE, damage=set_rows(b"not json\n")),
    State("rows-nested-4000-deep", REFUSED_A_ALONE,
          damage=set_rows(b"[" * 4000 + b"]" * 4000 + b"\n")),
    State("rows-not-an-object", REFUSED_A_ALONE, damage=set_rows(b'["a list"]\n')),
    State("rows-not-utf8", REFUSED_A_ALONE, damage=set_rows(b'{"why": "\xff"}\n')),
    State("deployment-parser-installed-since", {"a": "stale", "b": "stale"},
          seed={"no_yaml": True}),
    State("deployment-parser-removed-since", {"a": "stale", "b": "stale"},
          now={"no_yaml": True}),
    State("non-deployment-parser-installed-since", {"a": "stale", "b": "current"},
          estate=SPEC_ESTATE, seed={"no_yaml": True}),
    State("non-deployment-parser-removed-since", {"a": "stale", "b": "current"},
          estate=SPEC_ESTATE, now={"no_yaml": True}),
    State("tests-parser-installed-since", {"a": "stale", "b": "current"},
          estate=TESTS_ESTATE, seed={"no_python": True}),
    State("tests-parser-removed-since", {"a": "stale", "b": "current"},
          estate=TESTS_ESTATE, now={"no_python": True}),
    State("a-reader-gains-a-declaration", {"a": "stale", "b": "current"},
          damage=lambda where: edit_manifest(where, lambda m: m.update(parsers={}))),
    State("mixed-publish", {"a": "current", "b": "stale"}, damage=mixed_publish,
          now={"no_yaml": True}, unparsed={"compose-services": ["a"]}),
    State("emptied-sibling", {"a": "current", "b": "stale"}, damage=emptied_sibling),
    State("stale-and-its-parser-still-missing", {"a": "stale", "b": "stale"},
          damage=edit_file("a", "docker-compose.yml", compose(svc("b", "../b", "web-2"))),
          seed={"no_yaml": True}, now={"no_yaml": True}, unparsed={"compose-services": ["a"]}),
]


def plan_words(words: str) -> str:
    """Claim 6 as `--incremental` words it; every other reason as `--check-fresh` does."""
    return "its own files changed" if words.startswith(OWN_FILES) else words


@pytest.mark.parametrize("state", STATES, ids=[s.id for s in STATES])
def test_the_three_paths_agree_and_the_remedy_is_accepted(tmp_path, monkeypatch, state):
    lay_down(tmp_path, state.estate)
    publish(tmp_path, **state.seed)
    if state.damage is not None:
        state.damage(tmp_path)
    pre = preamble(os_refuses=state.os_refuses, **state.now)
    procs = {flag: spawn(tmp_path, flag, out, pre) for flag, out in (
        ("--check-fresh", "fresh"), ("--incremental", "inc"), ("--from-snapshot", "load"))}
    for flag, proc in procs.items():
        assert proc.stderr == "", f"{flag}: {proc.stderr}"
    with monkeypatch.context() as m, parsers(**state.now):
        if state.os_refuses:
            m.setattr(Path, "read_bytes", refusing(Path.read_bytes))
        fresh = B.check_freshness(tmp_path / "ws" / "workspace.toml")["freshness"]
        assert main(tmp_path, "full") == 0
    inc, load = report(tmp_path, "inc")["incremental"], report(tmp_path, "load")
    loaded, (note,) = load["snapshot"], load["notes"] or [""]
    current = [n for n, s in state.expect.items() if s == "current"]

    assert sorted(fresh["fresh"]) == current
    assert procs["--check-fresh"].returncode == (0 if len(current) == len(state.expect) else 2)
    assert fresh["unparsed"] == state.unparsed
    assert inc["reused"] == current
    assert procs["--incremental"].returncode == 0
    refused = sorted(n for n, s in state.expect.items() if s == "refused")
    assert loaded["mismatched"] == refused
    assert procs["--from-snapshot"].returncode == (2 if refused else 0)
    for name, expected in state.expect.items():
        if expected == "current":
            continue
        words = (fresh["refused"] if expected == "refused" else fresh["stale"])[name]
        assert inc["rebuilt"][name] == plan_words(words), name
        if expected == "missing":
            assert words == MISSING and name in loaded["missing"]
            assert f"no shard: {name}" in note
        elif expected == "refused":
            assert (f"refused - {words}" in note if words != SN.DIGEST_WORDS
                    else "refused for a digest mismatch" in note), note
        else:
            assert name in loaded["loaded"] and f"stale - {words}" in note, note
    for reader, comps in state.unparsed.items():
        assert (f"`{reader}` could not parse its files when its shard was published - no "
                f"parser installed: {', '.join(comps)}") in note
    if current == list(state.expect) and not state.unparsed:
        assert note == ""
    for name in ("graph.jsonl", "unresolved.jsonl"):
        assert (tmp_path / "inc" / name).read_bytes() == (tmp_path / "full" / name).read_bytes(), (
            f"{name}: an incremental build is a full build's, byte for byte")

    if state.remedy is not None:
        state.remedy(tmp_path)
    publish(tmp_path)  # followed: the parser installed, the OS reading, re-published
    after = spawn(tmp_path, "--check-fresh", "fresh-after")
    assert (after.returncode, after.stderr) == (0, ""), after.stdout + after.stderr
    assert sorted(B.check_freshness(tmp_path / "ws" / "workspace.toml")["freshness"]["fresh"]) \
        == sorted(state.expect)
    assert main(tmp_path, "load-after", "--from-snapshot") == 0
    assert report(tmp_path, "load-after")["notes"] == []


def _nested(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = {"x": value}
    return value


def test_a_row_nested_600_deep_is_loaded_reused_and_written_and_check_snapshot_drifts(tmp_path):
    """D16-46: a facts row `json.loads` accepts and `asdict` could not write - 600 deep - in a
    hand edit whose digest was recomputed. Like any such edit it is current to the stamps, so
    every path loads, reuses and writes it, and `--check-snapshot` is what reports the drift."""
    lay_down(tmp_path, FEED)
    publish(tmp_path)
    row = {"kind": "node", "id": "svc:deep", "type": "svc", "props": {"deep": _nested(600)}}
    append_facts(json.dumps(row, sort_keys=True).encode("utf-8"))(tmp_path)
    procs = {flag: spawn(tmp_path, flag, out) for flag, out in (
        ("--check-fresh", "fresh"), ("--incremental", "inc"), ("--from-snapshot", "load"))}
    assert [(p.returncode, p.stderr) for p in procs.values()] == [(0, "")] * 3
    assert report(tmp_path, "inc")["incremental"]["reused"] == ["a", "b"]
    for out in ("inc", "load"):
        assert '"id": "svc:deep"' in (tmp_path / out / "graph.jsonl").read_text(encoding="utf-8")
    drift = spawn(tmp_path, "--check-snapshot", "check")
    assert drift.returncode == 2 and drift.stderr == "", drift.stdout + drift.stderr
    assert "snapshot: DRIFT in a" in drift.stdout
    publish(tmp_path)  # followed
    assert spawn(tmp_path, "--check-snapshot", "check-after").returncode == 0


def test_a_line_of_an_unknown_kind_is_carried_as_a_row_on_every_path(tmp_path):
    """Review B16-2: a facts line of a kind no reader knows, in a hand edit whose digest was
    recomputed - current to the stamps like the cell above, so every path loads, reuses and
    writes it, and now each says it: the load and `--incremental` carry its row, named by its
    component and the shard's place in it. `--check-snapshot` reports the drift."""
    lay_down(tmp_path, FEED)
    publish(tmp_path)
    append_facts(b'{"kind": "future"}')(tmp_path)
    procs = {flag: spawn(tmp_path, flag, out) for flag, out in (
        ("--check-fresh", "fresh"), ("--incremental", "inc"), ("--from-snapshot", "load"))}
    assert [(p.returncode, p.stderr) for p in procs.values()] == [(0, "")] * 3
    for out in ("inc", "load"):
        rows = [json.loads(line) for line in
                (tmp_path / out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()]
        (row,) = [r for r in rows if r["kind"] == "unknown-record-kind"]
        assert (row["component"], row["source_file"]) == ("a", ".sdlc/graph/shard.jsonl"), out
    drift = spawn(tmp_path, "--check-snapshot", "check")
    assert drift.returncode == 2 and "snapshot: DRIFT in a" in drift.stdout
    publish(tmp_path)  # followed
    assert spawn(tmp_path, "--check-snapshot", "check-after").returncode == 0
