"""Batch 22 (T617's second half; design pass 22, section 3, the owner's item 2 = B): the
extractor version digests, besides each registered reader's name, layer and globs, the bytes of
the extraction path - option B's set - and of every registered reader's package, so a change to
a reader's code stales every shard it shaped (claim 4).

Each harness file is spelled relative to the harness root and each out-of-tree package's files
under its reader's registered name; bytes have `\\r\\n` folded to `\\n`. A member that cannot be
read, or is missing, makes the version UNKNOWN (`VersionUnknown`) - never digested as nothing.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness as F  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, producer, svc  # noqa: E402
from tools.graph.tests._harness_copy import copy_harness, spawn_copy  # noqa: E402

EXTRACTORS_CHANGED = "the extractors or their code changed since its shard was published"
FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}

#: Option B's named members (the note, section 3).
NAMED = {"tools/graph/__init__.py", "tools/graph/build.py", "tools/graph/freshness.py",
         "tools/graph/snapshot.py", "tools/graph/outdir.py", "tools/graph/core/__init__.py",
         "tools/graph/core/model.py", "tools/graph/core/spi.py"}
#: What cannot change a shard, left out (the note's list), and the named exclusions.
LEFT_OUT = ("tools/graph/links/", "tools/graph/mcp_server.py", "tools/graph/impact.py",
            "tools/graph/pack.py", "tools/graph/export_okf.py", "tools/graph/validate.py",
            "tools/graph/workspace_report.py", "tools/graph/reference_consumer.py",
            "tools/graph/rules.py", "tools/graph/core/store/", "tools/graph/tests/",
            "tools/__init__.py", "tools/cli.py", "sdlc-init.py")

PACKAGE = '''from tools.graph.core.model import ExtractResult


class Reader:
    name = "{name}"
    INPUT_GLOBS = ("*.b22",)

    def extract(self, ctx):
        return ExtractResult()


EXTRACTORS = (Reader(),)
'''


def spellings(regs) -> list[str]:
    return [spelling for spelling, _path in B.version_files(regs)]


def py_under(rel: str) -> set[str]:
    """Every `.py` file under a harness directory, by rglob - not the implementation's walk."""
    return {p.relative_to(ROOT).as_posix() for p in (ROOT / rel).rglob("*.py")
            if "__pycache__" not in p.parts}


def package(where: Path, name: str, reader: str) -> Path:
    pkg = where / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(PACKAGE.replace("{name}", reader), encoding="utf-8")
    return pkg


def copy_set(dst: Path, regs, *, newline: bytes) -> Path:
    """The version's harness files copied under `dst`, every line ending `newline`."""
    for spelling, path in B.version_files(regs):
        if spelling.startswith("tools/"):
            target = dst / spelling
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
    return dst


def with_profile() -> list:
    seen: dict[str, str] = {}
    regs, _links = B.discover(None, "stack", seen)
    profile, _ = B.discover_package(B.PROFILE_ROOT / "orderflow", "profile", seen,
                                    root_label="profiles")
    return B.order_extractors(regs + profile)


def test_claim_4_says_the_code_too():
    assert F.EXTRACTORS_CHANGED == EXTRACTORS_CHANGED


def test_the_version_holds_option_bs_set_and_nothing_else():
    found = spellings(B.discover_extractors())
    assert found == sorted(found) and len(found) == len(set(found)), "sorted by spelling, once"
    assert set(found) == (NAMED | py_under("tools/graph/core/lib")
                          | py_under("tools/graph/extractors"))
    assert not [s for s in found if s.startswith(LEFT_OUT)]
    assert "tools/graph/profiles/__init__.py" not in found, "no profile selected"
    assert all(s.endswith(".py") and "\\" not in s and ":" not in s for s in found)


def test_a_profile_under_the_harnesss_own_profiles_brings_its_package_in():
    found = set(spellings(with_profile()))
    own = {s for s in py_under("tools/graph/profiles/orderflow")
           if "tests" not in s.split("/")[4:-1]}
    assert {"tools/graph/profiles/__init__.py"} | own <= found
    assert "tools/graph/profiles/orderflow/__init__.py" in found


def test_an_out_of_tree_package_is_spelled_by_its_reader_and_its_tests_left_out(tmp_path):
    pkg = package(tmp_path, "prof", "b22-reader")
    (pkg / "helper.py").write_text("X = 1\n", encoding="utf-8")
    (pkg / "data.yml").write_text("x: 1\n", encoding="utf-8")
    for rel in ("tests/test_x.py", "sub/tests/t.py", "sub/mod.py"):
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text("Y = 2\n", encoding="utf-8")
    regs, _links = B.discover_package(pkg, "profile", {}, root_label="profiles")
    assert [s for s in spellings(regs) if not s.startswith("tools/")] == [
        "b22-reader/__init__.py", "b22-reader/helper.py", "b22-reader/sub/mod.py"]
    assert "tools/graph/extractors/tests/__init__.py" in spellings(B.discover_extractors()), (
        "a reader whose package IS `tests/` is held whole")


def test_an_out_of_tree_package_named_like_a_harness_profile_digests_its_own_bytes(tmp_path):
    """D22-16: `Registration.module` is a label (`profiles/orderflow`), not a place - read as one
    it would digest the harness's own `orderflow`, never this package."""
    pkg = package(tmp_path, "orderflow", "b22-own-orderflow")
    regs = B.discover_extractors() + B.discover_package(pkg, "profile", {},
                                                        root_label="profiles")[0]
    assert [r.package for r in regs if r.name == "b22-own-orderflow"] == [pkg]
    found = spellings(regs)
    assert "b22-own-orderflow/__init__.py" in found
    assert not [s for s in found if s.startswith("tools/graph/profiles/")]
    before = B.extractor_version(regs)
    with (pkg / "__init__.py").open("a", encoding="utf-8") as fh:
        fh.write("# changed\n")
    assert B.extractor_version(regs) != before


def test_a_crlf_checkout_reads_the_same_version_and_a_changed_byte_another(tmp_path):
    regs = B.discover_extractors()
    lf = copy_set(tmp_path / "lf", regs, newline=b"\n")
    crlf = copy_set(tmp_path / "crlf", regs, newline=b"\r\n")
    assert b"\r\n" in (crlf / "tools/graph/build.py").read_bytes()
    assert b"\r\n" not in (lf / "tools/graph/build.py").read_bytes()
    assert B.extractor_version(regs, root=lf) == B.extractor_version(regs, root=crlf)
    assert B.extractor_version(regs, root=lf) == B.extractor_version(regs), (
        "the live checkout, whatever its line endings")
    reader = lf / "tools/graph/extractors/compose/__init__.py"
    data = reader.read_bytes()
    assert b"import" in data
    reader.write_bytes(data.replace(b"import", b"imporT", 1))   # one byte, the length kept
    assert B.extractor_version(regs, root=lf) != B.extractor_version(regs, root=crlf)


def test_a_named_member_removed_makes_the_version_unknown(tmp_path):
    """D22-101: every named member must exist - a missing one is never digested as nothing."""
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    (root / "tools/graph/outdir.py").unlink()
    with pytest.raises(B.VersionUnknown) as caught:
        B.extractor_version(regs, root=root)
    assert (caught.value.spelling, caught.value.cause, caught.value.missing) == (
        "tools/graph/outdir.py", "No such file or directory", True)


def test_a_tree_removed_makes_the_version_unknown_never_a_walk_of_nothing(tmp_path):
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    shutil.rmtree(root / "tools/graph/core/lib")
    with pytest.raises(B.VersionUnknown) as caught:
        B.extractor_version(regs, root=root)
    assert (caught.value.spelling, caught.value.cause, caught.value.missing) == (
        "tools/graph/core/lib", "No such file or directory", True)


def test_a_member_that_cannot_be_read_makes_the_version_unknown(tmp_path):
    """A directory where a member's file belongs: the read fails, the member exists."""
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    member = root / "tools/graph/snapshot.py"
    member.unlink()
    member.mkdir()
    with pytest.raises(B.VersionUnknown) as caught:
        B.extractor_version(regs, root=root)
    cause = "Permission denied" if os.name == "nt" else "Is a directory"   # branch and assert
    assert (caught.value.spelling, caught.value.cause, caught.value.missing) == (
        "tools/graph/snapshot.py", cause, False)


def test_a_directory_that_cannot_be_listed_makes_the_version_unknown(tmp_path, monkeypatch):
    """Never a walk that skips what it could not list."""
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    blocked = root / "tools/graph/extractors/helm"
    real = os.scandir

    def scandir(path="."):
        if Path(path) == blocked:
            raise PermissionError(13, "denied", str(path))
        return real(path)
    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(B.VersionUnknown) as caught:
        B.extractor_version(regs, root=root)
    assert (caught.value.spelling, caught.value.cause, caught.value.missing) == (
        "tools/graph/extractors/helm", "Permission denied", False)


def test_a_cause_is_worded_by_its_errno_never_the_oss_sentence_or_the_path():
    """D22-104 with T638's rule: no machine path, and no locale - a Windows error's own text is
    the OS's sentence in the OS's language."""
    assert B._cause(PermissionError(13, "Zugriff verweigert", "h/x.py", 5)) == (
        "Permission denied")
    assert B._cause(FileNotFoundError(2, "Datei nicht gefunden", "/h/x.py")) == (
        "No such file or directory")
    assert B._cause(OSError("no errno")) == "OSError"


@pytest.mark.parametrize("first, second", [("b22-alpha", "b22-zeta"),
                                           ("b22-zeta", "b22-alpha")])
def test_a_package_registering_two_readers_is_spelled_once_by_the_first_name(tmp_path, first,
                                                                            second):
    """The first in name order, whichever registers first."""
    pkg = tmp_path / "two"
    pkg.mkdir()
    body = PACKAGE.replace("{name}", first).replace(
        "EXTRACTORS = (Reader(),)",
        f'class Other(Reader):\n    name = "{second}"\n\n\nEXTRACTORS = (Reader(), Other())')
    (pkg / "__init__.py").write_text(body, encoding="utf-8")
    regs, _links = B.discover_package(pkg, "profile", {}, root_label="profiles")
    assert [r.name for r in regs] == [first, second]
    assert [s for s in spellings(regs) if not s.startswith("tools/")] == [
        "b22-alpha/__init__.py"]


def test_the_tree_holds_the_tests_reader_whole_where_it_is_not_registered():
    """An `--only` run registers fewer readers: the tree's own rule still holds
    `extractors/tests/`, a package of the tree, whole."""
    regs = [r for r in B.discover_extractors()
            if r.package.resolve() != (B.EXTRACTOR_ROOT / "tests").resolve()]
    assert len(regs) < len(B.discover_extractors())
    assert "tools/graph/extractors/tests/__init__.py" in spellings(regs)


def test_a_module_renamed_with_its_bytes_unchanged_moves_the_version(tmp_path):
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    before = B.extractor_version(regs, root=root)
    chart = root / "tools/graph/extractors/helm/_chart.py"
    chart.rename(chart.with_name("_chart2.py"))
    assert B.extractor_version(regs, root=root) != before


def test_a_registration_with_no_package_is_refused():
    """D22-38: never digested as nothing."""
    bare = B.Registration(name="bare", layer="stack", globs=("*.x",), extractor=None,
                          package=None)
    with pytest.raises(B.BuildError, match="`bare`"):
        B.extractor_version([bare])


def test_a_module_dropped_into_a_tree_moves_the_version_and_one_beside_the_set_does_not(
        tmp_path):
    """An untracked `.py` counts where the set's trees reach; outside them - `tools/graph/`
    itself - option B leaves it out (its reach, stated)."""
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    before = B.extractor_version(regs, root=root)
    (root / "tools/graph/stray.py").write_text("X = 1\n", encoding="utf-8")
    assert B.extractor_version(regs, root=root) == before
    (root / "tools/graph/extractors/stray.py").write_text("X = 1\n", encoding="utf-8")
    assert B.extractor_version(regs, root=root) != before


def test_a_file_that_is_not_python_moves_nothing(tmp_path):
    """D22-87: a profile's `traversal_rules.yml` sets traversal weights, never a shard."""
    regs = with_profile()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    before = B.extractor_version(regs, root=root)
    (root / "tools/graph/profiles/orderflow/traversal_rules.yml").write_text(
        "weights: {}\n", encoding="utf-8")
    assert B.extractor_version(regs, root=root) == before


def test_a_readers_code_changed_stales_every_shard_at_check_fresh(tmp_path):
    """The door, spawned by path from a copy of the harness: published, current; the reader's
    module bytes changed; every shard stale with claim 4's words."""
    harness = copy_harness(tmp_path / "harness")
    lay_down(tmp_path, FEED)
    published = spawn_copy(harness, tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    fresh = spawn_copy(harness, tmp_path, "--check-fresh", out="c1")
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    reader = harness / "tools/graph/extractors/compose/__init__.py"
    reader.write_bytes(reader.read_bytes() + b"\n# a change to the reader's code\n")
    proc = spawn_copy(harness, tmp_path, "--check-fresh", out="c2")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    stale = sorted(line for line in proc.stdout.splitlines()
                   if line.startswith("freshness: STALE"))
    assert stale == [f"freshness: STALE {c} - {EXTRACTORS_CHANGED}" for c in ("a", "b")]
