"""Batch 22 (T617; design pass 22, section 3): a file of the extraction code that cannot be read,
or is missing, makes the extractor version UNKNOWN - and each door answers it as its job asks.

- the publish refuses before extracting anything, naming the file and the order its way on
  needs: "a file of the extraction code could not be read: `<path>` (<cause>) - <way on>,
  then publish again";
- the judging doors read it as claim 4 failing for every shard that reaches claim 4 - a
  missing, refused or unstamped shard keeps its own verdict - "a file of the extraction code
  could not be read: `<path>` (<cause>; <way on>)": `--check-fresh` exits 2, a load serves
  every shard as published, narrowed, `--incremental` rebuilds;
- a plain build completes, `manifest.json` records `"unreadable"` and the words are a note;
- `--check-snapshot` computes none.

Cell 1's member, `freshness.py` - which `build.py` imports at start, served from the bytecode
compiled in the copy by its first publish - is made really unreadable in a COPY of the tree:
an exclusive byte-range lock held by a child process on Windows, mode 000 on Linux (as an
unprivileged user where the suite runs as root), branched and asserted, never skipped; each
door is spawned by path from the copy, as an adopter runs it.
"""
from __future__ import annotations

import compileall
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import snapshot as SN  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, producer, svc  # noqa: E402
from tools.graph.tests._harness_copy import copy_harness  # noqa: E402
from tools.graph.tests.test_report_hygiene import strings  # noqa: E402

FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
MEMBER = "tools/graph/freshness.py"
UNKNOWN = (f"a file of the extraction code could not be read: `{MEMBER}` (Permission denied; "
           f"make it readable)")
REFUSAL = (f"!! a file of the extraction code could not be read: `{MEMBER}` (Permission "
           f"denied) - make it readable, then publish again")
#: Holds an exclusive lock on every byte of the file it is handed until its stdin closes.
LOCKER = ("import msvcrt, os, sys\n"
          "f = open(sys.argv[1], 'r+b')\n"
          "msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, os.path.getsize(sys.argv[1]))\n"
          "print('locked', flush=True)\n"
          "sys.stdin.read()\n")
#: The unprivileged user a door runs as where the suite runs as root (`nobody`).
NOBODY = 65534


def as_root() -> bool:
    return os.name != "nt" and os.geteuid() == 0


@pytest.fixture
def site(tmp_path):
    """Where the copy and the fixture live: pytest's temp dir, or - where the suite runs as
    root and the door as `nobody` - a directory outside pytest's `0o700` temp root."""
    if not as_root():
        yield tmp_path
        return
    base = Path(tempfile.mkdtemp(prefix="b22_unknown_", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class Doors:
    """The copy's doors over one workspace, spawned by path - as `nobody` under root."""

    def __init__(self, site: Path, estate: dict):
        self.site = site
        lay_down(site, estate)
        self.harness = copy_harness(site / "harness")
        self.member = self.harness / MEMBER
        # The member's bytecode, compiled in the copy first: the door imports it from there,
        # validated by a `stat` of the source, never a read (cell 1). Compiled explicitly - a
        # door writes no bytecode where PYTHONDONTWRITEBYTECODE is set.
        compileall.compile_dir(str(self.harness / "tools"), quiet=1)
        assert list((self.harness / "tools/graph/__pycache__").glob("freshness.*.pyc"))
        self.kwargs: dict = {}
        if as_root():
            for path in [site, *site.rglob("*")]:
                os.chown(path, NOBODY, NOBODY, follow_symlinks=False)
            self.kwargs = {"user": NOBODY, "group": NOBODY}

    def run(self, *flags: str, out: str = "out") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.harness / "tools" / "graph" / "build.py"),
             "--workspace", str(self.site / "ws" / "workspace.toml"),
             "--out", str(self.site / out), "--run-id", "fixed", "--empty-ok", *flags],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(self.harness), **self.kwargs)

    def shard_bytes(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.site)): p.read_bytes()
                for p in sorted((self.site / "ws").rglob("*")) if p.is_file()
                and SN.SHARD_DIR.as_posix() in p.as_posix()}

    @contextmanager
    def unreadable(self):
        """`freshness.py` really unreadable while the block runs, the premise asserted: the
        read fails, as the door's user."""
        path = self.member
        if os.name == "nt":
            locker = subprocess.Popen([sys.executable, "-c", LOCKER, str(path)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      text=True)
            try:
                assert locker.stdout.readline().strip() == "locked"
                with pytest.raises(PermissionError):
                    path.read_bytes()
                yield
            finally:
                locker.stdin.close()
                locker.wait(timeout=60)
            return
        mode = path.stat().st_mode
        os.chmod(path, 0)
        try:
            probe = subprocess.run(
                [sys.executable, "-c", f"open({str(path)!r}, 'rb').read()"],
                capture_output=True, text=True, **self.kwargs)
            assert probe.returncode != 0 and "PermissionError" in probe.stderr, (
                probe.returncode, probe.stderr)
            yield
        finally:
            os.chmod(path, mode)


def lines(proc: subprocess.CompletedProcess, prefix: str) -> list[str]:
    return sorted(line for line in proc.stdout.splitlines() if line.startswith(prefix))


def no_coordinates(doc, *where: Path) -> None:
    found = list(strings(doc))
    assert found, "the sweep looked at nothing"
    for coordinate in where:
        assert not [v for v in found if str(coordinate) in v], coordinate


def test_every_door_answers_a_file_of_the_code_that_cannot_be_read(site):
    doors = Doors(site, {**FEED, "c": {"README.md": "c\n"}})
    published = doors.run("--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    shard = site / "ws" / "c" / SN.SHARD_DIR / SN.SHARD
    shard.write_bytes(shard.read_bytes() + b"\n")          # c's shard refused, by its digest
    asked = doors.run("--check-snapshot", out="asked")
    before = doors.shard_bytes()

    with doors.unreadable():
        fresh = doors.run("--check-fresh", out="fresh")
        assert fresh.returncode == 2, fresh.stdout + fresh.stderr
        assert lines(fresh, "freshness: STALE") == [
            f"freshness: STALE {c} - {UNKNOWN}" for c in ("a", "b")]
        assert lines(fresh, "freshness: REFUSED") == [
            f"freshness: REFUSED c - {SN.DIGEST_WORDS}; re-publish (build --publish-snapshot)"]

        load = doors.run("--from-snapshot", out="load")
        assert f"stale - {UNKNOWN}: a, b" in load.stdout, load.stdout
        assert "refused for a digest mismatch: c" in load.stdout, load.stdout

        again = doors.run("--check-snapshot", out="asked")
        assert (again.returncode, lines(again, "snapshot:")) == (
            asked.returncode, lines(asked, "snapshot:")), "--check-snapshot computes none"

        # The REFUSED verdict's remedy and the load's re-publish, followed: the order.
        refused = doors.run("--publish-snapshot", out="refused")
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert REFUSAL in refused.stdout.splitlines(), refused.stdout
        assert "Traceback" not in refused.stderr
        assert doors.shard_bytes() == before, "nothing published"
        assert not (site / "refused" / "graph.jsonl").exists(), "nothing extracted or written"

        plain = doors.run(out="plain")
        assert plain.returncode == 0, plain.stdout + plain.stderr
        assert UNKNOWN in plain.stdout.splitlines(), plain.stdout
        manifest = json.loads((site / "plain" / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((site / "plain" / "build_report.json").read_text(encoding="utf-8"))
        assert manifest["extractor_version"] == "unreadable"
        assert UNKNOWN in report["notes"]
        load_report = json.loads((site / "load" / "build_report.json")
                                 .read_text(encoding="utf-8"))
        for doc in (manifest, report, load_report):
            no_coordinates(doc, site, doors.harness, ROOT)

    # Once it reads: the publish accepted (the way on, followed), then every door as before.
    followed = doors.run("--publish-snapshot", out="followed")
    assert followed.returncode == 0, followed.stdout + followed.stderr
    fresh = doors.run("--check-fresh", out="fresh")
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    stamped = json.loads((site / "ws" / "a" / SN.SHARD_DIR / SN.MANIFEST)
                         .read_text(encoding="utf-8"))["extractor_version"]
    recorded = json.loads((site / "followed" / "manifest.json")
                          .read_text(encoding="utf-8"))["extractor_version"]
    assert stamped == recorded != "unreadable", "the one value the publish checked"


def test_a_load_whose_only_reason_carries_its_way_on_offers_no_other(site):
    """One channel per line (D22-79): the unknown version's words carry their own way on, so
    a load narrowed by it alone offers no re-publish beside them."""
    doors = Doors(site, FEED)
    assert doors.run("--publish-snapshot").returncode == 0
    with doors.unreadable():
        load = doors.run("--from-snapshot", out="load")
    assert load.returncode == 0, load.stdout + load.stderr
    narrowed = [line for line in load.stdout.splitlines()
                if line.startswith("this load was narrowed")]
    assert len(narrowed) == 1, load.stdout
    assert f"stale - {UNKNOWN}: a, b" in narrowed[0]
    assert narrowed[0].endswith("To judge both: do what each reason says."), narrowed[0]
    assert "re-publish the shards it names" not in narrowed[0]


def test_incremental_rebuilds_every_shard_that_reaches_claim_4(site):
    doors = Doors(site, FEED)
    assert doors.run("--publish-snapshot").returncode == 0
    with doors.unreadable():
        inc = doors.run("--incremental", out="inc")
    assert inc.returncode == 0, inc.stdout + inc.stderr
    plan = json.loads((site / "inc" / "build_report.json")
                      .read_text(encoding="utf-8"))["incremental"]
    assert plan["reused"] == []
    assert plan["rebuilt"] == {"a": UNKNOWN, "b": UNKNOWN}
    manifest = json.loads((site / "inc" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["extractor_version"] == "unreadable"


MCP_PROBE = """
import json, sys
from pathlib import Path
harness, workspace = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(harness))
from tools.graph.mcp_server import GraphService
service = GraphService(harness, workspace=workspace)
first = service.check_fresh()
(harness / "tools" / "graph" / "outdir.py").unlink()
second = service.check_fresh()
print(json.dumps({"first": first["freshness"], "second": second["freshness"]}))
"""


def test_a_member_removed_after_the_mcp_door_imported_it_says_restore_it(tmp_path):
    """D22-101, D22-110, D22-123: the server's process imported `outdir.py` on its first tool
    call; removed since, the version is unknown, and its way on is to restore the file -
    never "make it readable", an act no one can do for a file that is not there."""
    doors = Doors(tmp_path, FEED)
    assert doors.run("--publish-snapshot").returncode == 0
    proc = subprocess.run([sys.executable, "-c", MCP_PROBE, str(doors.harness),
                           str(tmp_path / "ws" / "workspace.toml")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(doors.harness))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    assert (seen["first"]["fresh"], seen["first"]["stale"]) == (["a", "b"], {})
    words = ("a file of the extraction code could not be read: `tools/graph/outdir.py` (No "
             "such file or directory; restore it (check the harness out again))")
    assert seen["second"]["stale"] == {"a": words, "b": words}
    assert seen["second"]["remedies"] == {}, "the remedies dict holds refused verdicts alone"


def test_an_out_of_tree_file_gone_when_it_is_read_says_restore_it_alone(tmp_path, monkeypatch):
    """Listed, then gone before it is read: still the adopter's file, never the harness's."""
    import tools.graph.build as B
    pkg = tmp_path / "prof"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from tools.graph.core.model import ExtractResult\n\n\nclass R:\n    name = 'b22-r'\n"
        "    INPUT_GLOBS = ('*.r',)\n\n    def extract(self, ctx):\n        return ExtractResult()"
        "\n\n\nEXTRACTORS = (R(),)\n", encoding="utf-8")
    regs = B.discover_extractors() + B.discover_package(pkg, "profile", {},
                                                        root_label="profiles")[0]
    real = Path.read_bytes

    def read(self):
        if self.name == "__init__.py" and self.parent.name == "prof":
            raise FileNotFoundError(2, "gone", str(self))
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(B.VersionUnknown) as caught:
        B.extractor_version(regs)
    assert (caught.value.spelling, caught.value.missing, caught.value.own) == (
        "b22-r/__init__.py", True, False)
    assert B.unknown_words(caught.value).endswith("(No such file or directory; restore it)")


def test_an_out_of_tree_readers_missing_file_says_restore_it_alone(tmp_path):
    """D22-117: "(check the harness out again)" names the harness's own files, never an
    out-of-tree reader's package, which is the adopter's."""
    import tools.graph.build as B
    pkg = tmp_path / "prof"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from tools.graph.core.model import ExtractResult\n\n\nclass R:\n    name = 'b22-r'\n"
        "    INPUT_GLOBS = ('*.r',)\n\n    def extract(self, ctx):\n        return ExtractResult()"
        "\n\n\nEXTRACTORS = (R(),)\n", encoding="utf-8")
    (pkg / "helper.py").write_text("X = 1\n", encoding="utf-8")
    regs = B.discover_extractors() + B.discover_package(pkg, "profile", {},
                                                        root_label="profiles")[0]
    assert B.extractor_version(regs)
    shutil.rmtree(pkg)
    with pytest.raises(B.VersionUnknown) as caught:
        B.extractor_version(regs)
    assert B.unknown_words(caught.value) == (
        "a file of the extraction code could not be read: `b22-r` (No such file or "
        "directory; restore it)")
    assert B.publish_refusal(caught.value) == (
        "a file of the extraction code could not be read: `b22-r` (No such file or "
        "directory) - restore it, then publish again")
