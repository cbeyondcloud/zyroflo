"""Batch 22 (T617's second half; design pass 22, section 3, the owner's item 2 = B): the
extractor version digests, besides each registered reader's name, layer and globs, the bytes of
the extraction path - option B's set - and of every registered reader's package, so a change to
a reader's code stales every shard it shaped (claim 4).

Each harness file is spelled relative to the harness root and each out-of-tree package's files
under its reader's registered name; bytes have `\\r\\n` folded to `\\n`. A member that cannot be
read, or is missing, makes the version UNKNOWN (`VersionUnknown`) - never digested as nothing.
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness as F  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, producer, spawn, svc  # noqa: E402
from tools.graph.tests._harness_copy import copy_harness, spawn_copy  # noqa: E402
from tools.graph.tests._links import link, loop, remove, unlink  # noqa: E402

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
    """The version's harness files copied under `dst`, every line ending `newline` - never a
    directory walked under another spelling, which has no bytes (review round 3)."""
    for spelling, path in B.version_files(regs):
        if spelling.startswith("tools/") and not isinstance(path, B.WalkedAs):
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


def test_a_readers_name_that_spells_a_harness_file_is_refused(tmp_path):
    """Review round 1 (the reviewer's suspected cell, reproduced): an out-of-tree reader named
    `tools/graph` spelled its `build.py` as the harness's own, and the version digested the
    reader's file in place of the harness's - a change to `build.py` went unseen. Two files
    spelled alike are refused, never one dropped."""
    pkg = package(tmp_path, "prof", "tools/graph")
    (pkg / "build.py").write_text("X = 1\n", encoding="utf-8")
    regs = B.discover_extractors() + B.discover_package(pkg, "profile", {},
                                                        root_label="profiles")[0]
    with pytest.raises(B.BuildError) as refused:
        B.version_files(regs)
    assert ("`tools/graph`'s package file `build.py` is spelled `tools/graph/build.py`, as a "
            "file of the harness's extraction code is") in str(refused.value)
    assert "rename the reader" in str(refused.value)


@pytest.mark.parametrize("flags", [(), ("--publish-snapshot",), ("--incremental",),
                                   ("--check-fresh",), ("--from-snapshot",)],
                         ids=["plain", "publish", "incremental", "check-fresh", "from-snapshot"])
def test_the_collision_is_refused_at_every_door_before_anything_is_written(tmp_path, flags):
    """The refusal above as a person meets it: a workspace selecting a profile whose reader is
    named `tools/graph` - every door that computes the version exits 2 with the refusal, no
    traceback, and writes nothing; a plain build is refused before it extracts, never after its
    graph is written. (`--check-snapshot` computes none, D22-98, so it never meets it.)"""
    toml = lay_down(tmp_path, FEED)
    toml.write_text(toml.read_text(encoding="utf-8").replace('profile = ""',
                                                             'profile = "./prof"'),
                    encoding="utf-8")
    pkg = package(toml.parent, "prof", "tools/graph")
    (pkg / "build.py").write_text("X = 1\n", encoding="utf-8")
    proc = spawn(tmp_path, *flags)
    said = proc.stdout + proc.stderr
    assert proc.returncode == 2, said
    assert "Traceback" not in said, said
    assert ("!! `tools/graph`'s package file `build.py` is spelled `tools/graph/build.py`, as a "
            "file of the harness's extraction code is, so the extractor version could not tell "
            "them apart - rename the reader") in proc.stdout, said
    assert not (tmp_path / "out").exists(), sorted(
        p.name for p in (tmp_path / "out").iterdir())


def test_check_snapshot_computes_no_version_so_the_collision_never_reaches_it(tmp_path):
    """D22-98: `--check-snapshot` computes none - it answers its own question (here, that no
    shard is committed), the collision's refusal never among its words."""
    toml = lay_down(tmp_path, FEED)
    toml.write_text(toml.read_text(encoding="utf-8").replace('profile = ""',
                                                             'profile = "./prof"'),
                    encoding="utf-8")
    pkg = package(toml.parent, "prof", "tools/graph")
    (pkg / "build.py").write_text("X = 1\n", encoding="utf-8")
    proc = spawn(tmp_path, "--check-snapshot")
    said = proc.stdout + proc.stderr
    assert "Traceback" not in said, said
    assert "rename the reader" not in said, said
    assert [line for line in proc.stdout.splitlines() if line.startswith("snapshot: ")][:2] == [
        "snapshot: a has no committed shard to check",
        "snapshot: b has no committed shard to check"], said


def test_a_directory_link_in_a_readers_package_is_followed_once(tmp_path):
    """Review round 1 (suspected, reproduced on Windows): `os.walk` entered a junction but
    would not follow a POSIX symlink, so one tree gave two versions by platform; and a link
    back to the package descended until the path broke, the version UNKNOWN - every door saying
    a file "could not be read". A directory link is followed, as the reader's own import
    follows it, and each real directory is walked once - a second spelling of one recorded as
    the spelling it was walked under (review round 2)."""
    pkg = package(tmp_path, "prof", "b22-linked")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "extra.py").write_text("X = 1\n", encoding="utf-8")
    link(elsewhere, pkg / "linked")
    link(pkg, pkg / "loop")
    try:
        regs = registered(pkg)
        found = mine(regs, "b22-linked/")
        before = B.extractor_version(regs)
        (elsewhere / "extra.py").write_text("X = 2\n", encoding="utf-8")
        after = B.extractor_version(regs)
    finally:
        unlink(pkg / "loop")
        unlink(pkg / "linked")
    assert found == {"b22-linked/__init__.py": pkg / "__init__.py",
                     "b22-linked/linked/extra.py": pkg / "linked" / "extra.py",
                     "b22-linked/loop": B.WalkedAs("b22-linked")}, found
    assert before != after, "a change behind the link moves the version"


def registered(pkg: Path) -> list:
    return B.discover_extractors() + B.discover_package(pkg, "profile", {},
                                                        root_label="profiles")[0]


def mine(regs, prefix: str) -> dict:
    return {s: p for s, p in B.version_files(regs) if s.startswith(prefix)}


def test_a_dangling_directory_link_makes_the_version_unknown_on_every_platform(tmp_path):
    """Review round 2 (B22-8): a link whose directory is gone was a directory the walk could
    not list on Windows (a junction) and a file not ending `.py`, dropped without a word, on
    POSIX (a symlink). Both now make the version unknown, naming the link - D22-101's reading
    of what the walk cannot list."""
    pkg = package(tmp_path, "prof", "b22-dangling")
    gone = tmp_path / "gone"
    gone.mkdir()
    link(gone, pkg / "linked")
    gone.rmdir()
    try:
        with pytest.raises(B.VersionUnknown) as caught:
            B.extractor_version(registered(pkg))
    finally:
        unlink(pkg / "linked")
    assert (caught.value.spelling, caught.value.cause, caught.value.missing,
            caught.value.own) == ("b22-dangling/linked", "No such file or directory", True, False)


def test_a_dangling_link_named_tests_in_a_package_is_left_out_as_its_directory_is(tmp_path):
    """The exclusions come first: a package's `tests/` is never digested, so a link of that
    name to nothing is left out too, never read as unknown."""
    pkg = package(tmp_path, "prof", "b22-tests-link")
    gone = tmp_path / "gone"
    gone.mkdir()
    link(gone, pkg / "tests")
    gone.rmdir()
    try:
        found = mine(registered(pkg), "b22-tests-link/")
    finally:
        unlink(pkg / "tests")
    assert found == {"b22-tests-link/__init__.py": pkg / "__init__.py"}, found


def test_a_module_moved_behind_a_link_moves_the_version(tmp_path):
    """Review round 2 (B22-9): `current -> v1` and `current -> v2`, the same bytes behind each -
    the module moved, and the version kept the link's spelling and pruned the real one's, so it
    did not move. The real directory's spelling is recorded as walked under the link's."""
    versions, files = [], []
    for vdir in ("v1", "v2"):
        pkg = package(tmp_path / vdir, f"prof_{vdir}", "b22-vbump")
        (pkg / vdir).mkdir()
        (pkg / vdir / "mod.py").write_text("X = 1\n", encoding="utf-8")
        link(pkg / vdir, pkg / "current")
        try:
            regs = registered(pkg)
            versions.append(B.extractor_version(regs))
            files.append({s: p for s, p in mine(regs, "b22-vbump/").items()
                          if s != "b22-vbump/__init__.py"})
        finally:
            unlink(pkg / "current")
    assert versions[0] != versions[1], "the module moved"
    assert files == [{"b22-vbump/current/mod.py": tmp_path / "v1/prof_v1/current/mod.py",
                      "b22-vbump/v1": B.WalkedAs("b22-vbump/current")},
                     {"b22-vbump/current/mod.py": tmp_path / "v2/prof_v2/current/mod.py",
                      "b22-vbump/v2": B.WalkedAs("b22-vbump/current")}], files


def test_a_directory_and_a_link_to_it_digest_apart_from_the_directory_alone(tmp_path):
    """Review round 2 (B22-9): a real `zdir/mod.py` with `adir -> zdir` digested as a plain
    `adir/mod.py` - renaming the directory onto the link's name was invisible to claim 4."""
    linked = package(tmp_path / "a", "prof_a", "b22-plain")
    (linked / "zdir").mkdir()
    (linked / "zdir" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    link(linked / "zdir", linked / "adir")
    plain = package(tmp_path / "b", "prof_b", "b22-plain")
    (plain / "adir").mkdir()
    (plain / "adir" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    try:
        with_link = B.extractor_version(registered(linked))
    finally:
        unlink(linked / "adir")
    assert with_link != B.extractor_version(registered(plain))


def test_a_link_retargeted_between_two_kept_directories_moves_the_version(tmp_path):
    """Both directories kept, the link moved from one to the other: the code it imports changed,
    so the version moves - skipping a link into its own package, and walking its directory under
    the real spelling alone, would leave it equal."""
    pkg = package(tmp_path, "prof", "b22-retarget")
    for vdir, body in (("v1", "X = 1\n"), ("v2", "X = 2\n")):
        (pkg / vdir).mkdir()
        (pkg / vdir / "mod.py").write_text(body, encoding="utf-8")
    regs = registered(pkg)
    link(pkg / "v1", pkg / "current")
    try:
        on_v1 = B.extractor_version(regs)
    finally:
        unlink(pkg / "current")
    link(pkg / "v2", pkg / "current")
    try:
        on_v2 = B.extractor_version(regs)
    finally:
        unlink(pkg / "current")
    assert on_v1 != on_v2


def test_two_links_to_one_directory_and_a_cycle_through_two_links(tmp_path):
    """A directory outside the package reached by two links is walked once, under the first
    spelling in walk order, the second recorded as walked there; a cycle through two links -
    out of the package and back into it - ends, recorded the same way."""
    pkg = package(tmp_path, "prof", "b22-two")
    other = tmp_path / "other"
    other.mkdir()
    (other / "mod.py").write_text("X = 1\n", encoding="utf-8")
    link(other, pkg / "aaa")
    link(other, pkg / "zzz")
    link(pkg, other / "back")
    try:
        found = mine(registered(pkg), "b22-two/")
    finally:
        unlink(other / "back")
        unlink(pkg / "zzz")
        unlink(pkg / "aaa")
    assert found == {"b22-two/__init__.py": pkg / "__init__.py",
                     "b22-two/aaa/back": B.WalkedAs("b22-two"),
                     "b22-two/aaa/mod.py": pkg / "aaa" / "mod.py",
                     "b22-two/zzz": B.WalkedAs("b22-two/aaa")}, found


def test_copy_set_copies_files_and_never_a_directory_walked_under_another_spelling(tmp_path):
    """Review round 3 (B22-10): the helper read every value of `version_files` as a file, so a
    `WalkedAs` answered a traceback; it has no bytes to copy."""
    pkg = package(tmp_path, "prof", "tools/x")
    (pkg / "real").mkdir()
    (pkg / "real" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    link(pkg / "real", pkg / "zzz")
    try:
        regs = registered(pkg)
        assert mine(regs, "tools/x/")["tools/x/zzz"] == B.WalkedAs("tools/x/real")
        dst = copy_set(tmp_path / "copy", regs, newline=b"\n")
    finally:
        unlink(pkg / "zzz")
    assert (dst / "tools/x/real/mod.py").read_bytes() == b"X = 1\n"
    assert not (dst / "tools/x/zzz").exists()


LOOPED_WORDS = ("a file of the extraction code could not be read: `{}` (Too many levels of "
                "symbolic links; remove or retarget the link)")


def test_a_link_that_never_ends_is_read_from_both_platforms_answers():
    """Review rounds 3 and 4 (B22-11, B22-14): a link that never ends is ELOOP on POSIX and, on
    Windows, ERROR_CANT_RESOLVE_FILENAME (1921) in `winerror`, which Python reads as EINVAL -
    "Invalid argument"; a file that is missing or denied is neither. Its cause is one chosen word
    on both platforms, and its way on: remove or retarget the link."""
    assert B._loops(OSError(errno.ELOOP, "loop"))
    assert B._cause(OSError(errno.ELOOP, "loop")) == "Too many levels of symbolic links"
    assert not B._loops(FileNotFoundError(errno.ENOENT, "gone"))
    assert not B._loops(PermissionError(errno.EACCES, "denied"))
    resolve = OSError(0, "cannot resolve", None, 1921)
    if os.name == "nt":
        assert resolve.winerror == 1921 and resolve.errno == errno.EINVAL and B._loops(resolve)
        assert B._cause(resolve) == "Too many levels of symbolic links"
    else:
        assert not hasattr(resolve, "winerror") and not B._loops(resolve)
    looped = B.VersionUnknown("b22-loop/a", "Too many levels of symbolic links", False, False,
                              looped=True)
    assert B.unknown_words(looped) == LOOPED_WORDS.format("b22-loop/a")
    assert B.publish_refusal(looped) == (
        "a file of the extraction code could not be read: `b22-loop/a` (Too many levels of "
        "symbolic links) - remove or retarget the link, then publish again")


def test_a_link_that_never_ends_says_remove_or_retarget_it_and_that_works(tmp_path):
    """Review rounds 3 and 4 (B22-11, B22-13, B22-14): an entry of a package that is a link that
    never ends - a symlink to itself on POSIX; on Windows, unprivileged, a chain of junctions
    longer than the system resolves - makes the version unknown, worded alike on both, its way
    on "remove or retarget the link" ("make it readable" changed nothing), and each half of it,
    followed, lets the version compute."""
    pkg = package(tmp_path, "prof", "b22-loop")
    regs = registered(pkg)
    made = loop(pkg / "a", tmp_path / "chain")
    try:
        with pytest.raises(B.VersionUnknown) as caught:
            B.extractor_version(regs)
    finally:
        remove(made)
    assert (caught.value.spelling, caught.value.missing, caught.value.looped) == (
        "b22-loop/a", False, True)
    assert B.unknown_words(caught.value) == LOOPED_WORDS.format("b22-loop/a")
    assert B.extractor_version(regs), "removed, it computes"
    real = tmp_path / "real"
    real.mkdir()
    link(real, pkg / "a")
    try:
        assert B.extractor_version(regs), "retargeted, it computes"
    finally:
        unlink(pkg / "a")


def test_a_walked_directory_that_never_ends_is_unknown_with_its_way_on(tmp_path):
    """Review round 4 (B22-16): the walk's own refusal (`unlisted`) of a directory it cannot
    list carries `looped` too - its top here a link that never ends, on both platforms."""
    top = tmp_path / "top"
    made = loop(top, tmp_path / "chain")
    try:
        with pytest.raises(B.VersionUnknown) as caught:
            B._version_tree(top, "b22-top", package=True, own=False)
    finally:
        remove(made)
    assert B.unknown_words(caught.value) == LOOPED_WORDS.format("b22-top")
    assert (caught.value.missing, caught.value.looped) == (False, True)


def test_a_member_that_never_ends_is_unknown_with_its_way_on(tmp_path):
    """Review round 4 (B22-16): a named member is read without the walk's check, so its read
    (`extractor_version`) carries `looped` too - a file link to itself, where this process may
    make one: POSIX may; Windows needs a privilege for a file symlink (a junction is a directory,
    which reads as "Permission denied"), so without it the refusal is asserted instead."""
    regs = B.discover_extractors()
    root = copy_set(tmp_path / "h", regs, newline=b"\n")
    member = root / "tools/graph/outdir.py"
    member.unlink()
    try:
        os.symlink(member, member)
    except OSError as exc:
        assert os.name == "nt" and getattr(exc, "winerror", None) == 1314, exc
        return
    try:
        with pytest.raises(B.VersionUnknown) as caught:
            B.extractor_version(regs, root=root)
    finally:
        member.unlink()
    assert B.unknown_words(caught.value) == LOOPED_WORDS.format("tools/graph/outdir.py")
    assert caught.value.looped


def test_of_several_entries_it_cannot_follow_the_first_by_name_is_named(tmp_path):
    """Review rounds 4 and 5 (B22-15, B22-17): the walk checks a directory's entries in name
    order - by code point - so of several it cannot follow it names the first by name, never the
    first the filesystem lists: NTFS lists by upper-cased name (`a00` before `B00`, which sorts
    first), ext4 by hash, tmpfs newest first."""
    pkg = package(tmp_path, "prof", "b22-order")
    gone = tmp_path / "gone"
    gone.mkdir()
    names = [f"{c}{i:02d}" for c in "aB" for i in range(10)]
    for name in names:
        link(gone, pkg / name)
    gone.rmdir()
    # Where the filesystem lists the first by name first as well, it cannot tell the orders
    # apart: drop that entry until it does (the CI image's ext4 hash listed `e00` first of
    # `e00`..`e19`; NTFS lists `a00` first of these, never `B00`).
    listed = [n for n in os.listdir(pkg) if n in names]
    while len(listed) > 1 and listed[0] == min(listed):
        unlink(pkg / listed[0])
        names.remove(listed[0])
        listed = [n for n in os.listdir(pkg) if n in names]
    try:
        with pytest.raises(B.VersionUnknown) as caught:
            B.extractor_version(registered(pkg))
    finally:
        for name in names:
            unlink(pkg / name)
    assert caught.value.spelling == f"b22-order/{min(names)}", caught.value.spelling


def test_of_two_sibling_directories_the_first_by_name_is_walked_first(tmp_path):
    """Review round 5 (B22-18): the walk descends into a directory's subdirectories in name
    order too, so of two siblings each holding an entry it cannot follow, the first by name
    decides the refusal - as it decides the spelling a directory reached twice is walked under.
    NTFS lists `a` before `Z`; by code point `Z` comes first."""
    pkg = package(tmp_path, "prof", "b22-dir")
    gone = tmp_path / "gone"
    gone.mkdir()
    for sub in ("Z", "a"):
        (pkg / sub).mkdir()
        link(gone, pkg / sub / "x")
    gone.rmdir()
    try:
        with pytest.raises(B.VersionUnknown) as caught:
            B.extractor_version(registered(pkg))
    finally:
        for sub in ("Z", "a"):
            unlink(pkg / sub / "x")
    assert caught.value.spelling == "b22-dir/Z/x", caught.value.spelling


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
