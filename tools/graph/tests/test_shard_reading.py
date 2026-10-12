"""Batch 16 (T628): one reading of a shard, `snapshot.read_shard`, which never raises for a
damaged shard. Each refusal names its cause and a remedy that is accepted when followed;
a manifest's stamp block is checked by type inside `read_manifest`, so all four doors share
it; an absent rows file reads as an empty one, while a missing facts file is refused.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import snapshot as SN  # noqa: E402
from tools.graph.core.model import ExtractResult, Node  # noqa: E402

COMP = {"name": "a", "path": "a"}
STAMPS = {"inputs": {"compose-services": "abc"}, "extractor_version": "v1", "config_sha": "c1",
          "parsers": {"yaml": True}}
ROWS = [{"extractor": "x", "component": "a", "kind": "k", "source_file": "f", "line": 1,
         "why": "w"}]
MANIFEST_READ = "its committed snapshot.json exists but could not be read ("


def publish(root: Path, stamps: dict | None = STAMPS, rows: list | None = None) -> Path:
    facts = ExtractResult(nodes=[Node(id="svc:a", type="svc", props={})],
                          unresolved=list(ROWS if rows is None else rows))
    dir_ = SN.shard_dir(root, COMP)
    SN.write_shard(dir_, facts, stamps)
    return dir_


def restamp(dir_: Path) -> None:
    """Recompute the recorded digest so the bytes verify, as a hand edit would."""
    manifest = json.loads((dir_ / SN.MANIFEST).read_text(encoding="utf-8"))
    manifest["sha256"] = SN._digest(dir_)
    (dir_ / SN.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


def set_manifest(dir_: Path, edit) -> None:
    manifest = json.loads((dir_ / SN.MANIFEST).read_text(encoding="utf-8"))
    edit(manifest)
    (dir_ / SN.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


def test_a_healthy_shard_reads_whole(tmp_path):
    publish(tmp_path)
    reading = SN.read_shard(tmp_path, COMP)
    assert (reading.absent, reading.refused) == (None, None)
    assert reading.stamps["parsers"] == {"yaml": True}
    assert [n.id for n in reading.facts.nodes] == ["svc:a"]
    assert reading.facts.unresolved == ROWS


def test_nothing_at_the_manifest_path_is_no_shard(tmp_path):
    assert SN.read_shard(tmp_path, COMP).absent == "no shard"


def test_a_manifest_without_inputs_is_no_stamps_and_still_loads(tmp_path):
    """`test_damaged_graph.py`'s intact manifest is `{"sha256": ...}`: keyed on `inputs`,
    never on `schema_version` (design round 2, D16-25)."""
    dir_ = publish(tmp_path, stamps=None)
    (dir_ / SN.MANIFEST).write_text(json.dumps({"sha256": SN._digest(dir_)}), encoding="utf-8")
    reading = SN.read_shard(tmp_path, COMP)
    assert (reading.absent, reading.refused) == ("no stamps", None)
    assert [n.id for n in reading.facts.nodes] == ["svc:a"]


@pytest.mark.parametrize("raw, cause", [
    (b"<<<<<<< ours\n{}", "not JSON"),
    (b"[]", "not a JSON object"),
    (b'{"sha256": "\xff\xfe"}', "not UTF-8"),
    (b"[" * 4000 + b"]" * 4000, "not JSON"),
], ids=["conflict-marker", "a-list", "not-utf8", "nested-4000-deep"])
def test_a_manifest_that_cannot_be_read_is_refused_with_its_cause(tmp_path, raw, cause):
    dir_ = publish(tmp_path)
    (dir_ / SN.MANIFEST).write_bytes(raw)
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == f"{MANIFEST_READ}{cause})"
    assert (reading.refusal_kind, reading.remedy) == ("manifest", "re-publish")


@pytest.mark.parametrize("edit, cause", [
    (lambda m: m.pop("sha256"), "it records no digest"),
    (lambda m: m.__setitem__("inputs", "nonsense"), "`inputs` is not a mapping of strings"),
    (lambda m: m.__setitem__("inputs", {"x": 7}), "`inputs` is not a mapping of strings"),
    (lambda m: m.__setitem__("parsers", "yes"), "`parsers` is not a mapping of booleans"),
    (lambda m: m.__setitem__("parsers", {"yaml": "true"}),
     "`parsers` is not a mapping of booleans"),
    (lambda m: m.__setitem__("extractor_version", 7), "`extractor_version` is not a string"),
    (lambda m: m.__setitem__("config_sha", None), "`config_sha` is not a string"),
    (lambda m: m.__setitem__("sha256", 1), "`sha256` is not a string"),
], ids=["no-sha256", "inputs-a-string", "inputs-an-int", "parsers-a-string",
        "parsers-a-string-value", "version-an-int", "config-null", "sha256-an-int"])
def test_a_wrongly_typed_stamp_block_is_refused_by_name(tmp_path, edit, cause):
    """Design rounds 2 and 5 (D16-16, D16-51): these took three paths down with an
    `AttributeError`, or were told their bytes "no longer match" a digest never recorded."""
    dir_ = publish(tmp_path)
    set_manifest(dir_, edit)
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == f"{MANIFEST_READ}{cause})"
    assert reading.refusal_kind == "manifest"


def test_a_manifest_that_is_not_a_regular_file_is_refused_with_a_remedy_that_works(tmp_path):
    dir_ = publish(tmp_path)
    (dir_ / SN.MANIFEST).unlink()
    (dir_ / SN.MANIFEST).mkdir()
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == f"{MANIFEST_READ}not a regular file)"
    assert reading.remedy == "remove it, then re-publish"
    (dir_ / SN.MANIFEST).rmdir()  # followed
    publish(tmp_path)
    assert SN.read_shard(tmp_path, COMP).refused is None


def test_a_manifest_the_os_will_not_read_is_refused_with_its_cause(tmp_path, monkeypatch):
    """Design round 1 (D16-2): an `OSError` reading `snapshot.json` (a lock, a permission,
    an I/O error) crashed all four paths. It is refused with the system's words."""
    dir_ = publish(tmp_path)
    manifest = dir_ / SN.MANIFEST
    real = Path.read_bytes

    def locked(self):
        if self == manifest:
            raise PermissionError(13, "Permission denied")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", locked)
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == f"{MANIFEST_READ}Permission denied)"
    assert reading.remedy == "fix what blocks reading it, then re-publish"


@pytest.mark.parametrize("name", [SN.SHARD, SN.SHARD_UNRESOLVED])
def test_a_shard_file_that_cannot_be_read_is_named(tmp_path, name):
    dir_ = publish(tmp_path)
    (dir_ / name).unlink()
    (dir_ / name).mkdir()
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused.startswith(
        f"its shard file `a/.sdlc/graph/{name}` could not be read (")
    assert (reading.refusal_kind, reading.remedy) == ("bytes", "remove it, then re-publish")


def test_a_missing_facts_file_beside_a_manifest_is_refused(tmp_path):
    """Design round 4 (D16-37): `read_jsonl` read a missing file as empty and `_digest` hashed
    it as empty bytes, so a restamped shard with no facts loaded at exit 0, facts gone."""
    dir_ = publish(tmp_path)
    (dir_ / SN.SHARD).unlink()
    restamp(dir_)
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == "its shard file `a/.sdlc/graph/shard.jsonl` is missing"
    assert (reading.refusal_kind, reading.remedy) == ("missing-facts", "re-publish")


def test_an_absent_rows_file_reads_as_an_empty_one(tmp_path):
    dir_ = publish(tmp_path, rows=[])
    (dir_ / SN.SHARD_UNRESOLVED).unlink()
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused is None and reading.facts.unresolved == []


def test_a_digest_mismatch_keeps_both_renderings(tmp_path):
    dir_ = publish(tmp_path)
    (dir_ / SN.SHARD).write_bytes((dir_ / SN.SHARD).read_bytes() + b"\n")
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == "its shard bytes no longer match their recorded digest"
    assert reading.refused_load == ("its shard does not match its own recorded digest; a hand "
                                    "edit or a half-written publish. Rebuild and re-publish it")
    assert reading.refusal_kind == "digest"


@pytest.mark.parametrize("line, cause", [
    (b"not json", "not JSON"),
    (b"[" * 4000 + b"]" * 4000, "not JSON"),
    (b'["a list"]', "not a JSON object"),
    (b"null", "not a JSON object"),
    (b'{"kind": "node", "id": "svc:\xff", "type": "svc", "props": {}}', "not UTF-8"),
], ids=["not-json", "nested-4000-deep", "a-list", "null", "not-utf8"])
def test_facts_that_cannot_be_read_are_refused_with_their_cause(tmp_path, line, cause):
    dir_ = publish(tmp_path)
    (dir_ / SN.SHARD).write_bytes((dir_ / SN.SHARD).read_bytes() + line + b"\n")
    restamp(dir_)
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused.startswith("its shard's facts could not be read (")
    assert cause in reading.refused
    assert (reading.refusal_kind, reading.remedy) == ("facts", "re-publish")


@pytest.mark.parametrize("line, cause", [
    (b"not json", "line 2: not JSON"),
    (b"[" * 4000 + b"]" * 4000, "line 2: not JSON"),
    (b'["a list"]', "line 2: not a JSON object"),
    (b'{"why": "\xff"}', "not UTF-8"),
], ids=["not-json", "nested-4000-deep", "a-list", "not-utf8"])
def test_rows_that_cannot_be_read_are_refused_with_their_cause(tmp_path, line, cause):
    dir_ = publish(tmp_path)
    (dir_ / SN.SHARD_UNRESOLVED).write_bytes(
        (dir_ / SN.SHARD_UNRESOLVED).read_bytes() + line + b"\n")
    restamp(dir_)
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused == f"its shard's rows could not be read ({cause})"
    assert (reading.refusal_kind, reading.remedy) == ("rows", "re-publish")


def test_a_facts_refusal_names_the_shard_by_its_place_in_the_workspace(tmp_path):
    """The reader's own message carried the shard's absolute path, and the load's notes and
    `--incremental`'s reasons, which a build report commits, would have carried the
    machine's directory layout with it."""
    dir_ = publish(tmp_path)
    (dir_ / SN.SHARD).write_bytes((dir_ / SN.SHARD).read_bytes() + b"not json\n")
    restamp(dir_)
    reading = SN.read_shard(tmp_path, COMP)
    assert "(a/.sdlc/graph/shard.jsonl:3: not JSON (" in reading.refused
    assert str(tmp_path) not in reading.refused


@pytest.mark.parametrize("separator", [0x2028, 0x2029, 0x85], ids=["LS", "PS", "NEL"])
def test_a_shard_holding_a_unicode_line_separator_is_read_not_refused(tmp_path, separator):
    """T634: the writers emit these three raw (`ensure_ascii=False`; JSON escapes only the C0
    controls) and `str.splitlines()` ends a line at each, so a fact or a row holding one read
    back as an unterminated string - a traceback on `main`, and under batch 16 a refused
    shard whose remedy, a re-publish, wrote the same bytes again. Both readers split on the
    newline the writers join with."""
    text = f"x{chr(separator)}y"
    rows = [{**ROWS[0], "why": text}]
    facts = ExtractResult(nodes=[Node(id="svc:a", type="svc", props={"note": text})],
                          unresolved=rows)
    dir_ = SN.shard_dir(tmp_path, COMP)
    SN.write_shard(dir_, facts, STAMPS)
    assert chr(separator).encode("utf-8") in (dir_ / SN.SHARD).read_bytes(), "written raw"
    reading = SN.read_shard(tmp_path, COMP)
    assert reading.refused is None, reading.refused
    assert reading.facts.nodes == facts.nodes and reading.facts.unresolved == rows


def test_keep_facts_false_drops_nodes_and_edges_but_keeps_the_rows(tmp_path):
    publish(tmp_path)
    reading = SN.read_shard(tmp_path, COMP, keep_facts=False)
    assert reading.facts.nodes == [] and reading.facts.edges == []
    assert reading.facts.unresolved == ROWS


def test_a_shard_converted_to_crlf_is_refused_and_a_re_publish_restores_it(tmp_path):
    """Review focus 2: a checkout with no shard `.gitattributes` converts the shard to CRLF;
    the digest refuses it, and the remedy - a re-publish, which writes `.gitattributes` and LF
    bytes - is accepted."""
    dir_ = publish(tmp_path)
    (dir_ / SN.GITATTRIBUTES).unlink()
    shard = dir_ / SN.SHARD
    shard.write_bytes(shard.read_bytes().replace(b"\n", b"\r\n"))
    assert SN.read_shard(tmp_path, COMP).refusal_kind == "digest"
    publish(tmp_path)
    assert SN.read_shard(tmp_path, COMP).refused is None
    assert (dir_ / SN.GITATTRIBUTES).is_file() and b"\r\n" not in shard.read_bytes()


def test_stamps_of_reads_the_manifest_it_is_handed():
    assert SN.stamps_of(None) is None
    assert SN.stamps_of({"sha256": "x"}) is None
    assert SN.stamps_of({"sha256": "x", **STAMPS})["parsers"] == {"yaml": True}
    assert "parsers" not in SN.stamps_of({"sha256": "x", "inputs": {}})


def test_load_is_a_loop_over_the_reading(tmp_path):
    publish(tmp_path)
    other = {"name": "b", "path": "b"}
    damaged = {"name": "c", "path": "c"}
    dir_c = SN.shard_dir(tmp_path, damaged)
    SN.write_shard(dir_c, ExtractResult(), STAMPS)
    (dir_c / SN.MANIFEST).write_text("<<<<<<< ours", encoding="utf-8")
    merged, present, missing, mismatched = SN.load(tmp_path, [COMP, other, damaged])
    assert (present, missing, mismatched) == (["a"], ["b"], ["c"])
    assert merged.unresolved == ROWS


def test_check_snapshot_reports_an_unreadable_shard_file_as_could_not_look(tmp_path):
    """`_digest` raised for a shard file that is a directory, and `--check-snapshot` crashed
    (design round 4, D16-40); it is could-not-look, with the cause kept for its wording."""
    dir_ = publish(tmp_path)
    (dir_ / SN.SHARD).unlink()
    (dir_ / SN.SHARD).mkdir()
    drifted, unpublished, unreadable, uncomparable = SN.check(
        tmp_path, [COMP], {"a": ExtractResult()})
    assert (drifted, unpublished, uncomparable) == ([], [], [])
    assert unreadable == ["a"]
    assert SN.unreadable_cause(tmp_path, COMP).startswith(
        "its shard file `a/.sdlc/graph/shard.jsonl` could not be read (")


# ── `--check-snapshot`'s words, as a process ─────────────────────────────────

def test_check_snapshot_names_an_unreadable_shard_file_with_a_remedy_that_works(tmp_path):
    """Design round 4 (D16-40): `--check-snapshot` crashed on a shard file that is a
    directory; it is could-not-look with its own words, not the manifest's, and following its
    remedy (remove it, re-publish) is accepted."""
    import subprocess

    from tools.graph.tests._estate import build

    estate = {"a": {"charts/web/values.yaml": "fullnameOverride: web\n",
                    "charts/web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n"},
              "b": {"app.py": "print('b')\n"}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    shard = tmp_path / "ws" / "a" / ".sdlc" / "graph" / "shard.jsonl"
    shard.unlink()
    shard.mkdir()

    def spawn(*flags: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "tools.graph.build", "--workspace",
             str(tmp_path / "ws" / "workspace.toml"), "--out", str(tmp_path / "out"),
             "--run-id", "fixed", *flags],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")

    proc = spawn("--check-snapshot")
    assert proc.returncode == 2 and proc.stderr == "", proc.stdout + proc.stderr
    assert ("snapshot: REFUSING a - its shard file `a/.sdlc/graph/shard.jsonl` could not be "
            "read (not a regular file); this build cannot verify whether its shard matches - "
            "remove the non-file or fix what blocks reading it, then re-publish (build "
            "--publish-snapshot)") in proc.stdout
    shard.rmdir()  # followed
    assert spawn("--publish-snapshot").returncode == 0
    assert spawn("--check-snapshot").returncode == 0
