"""T645 (batch 27): a judgment of absence is never made over a directory nobody could list.

`canonical-unmatched` and `apart-unmatched` say the graph read no such deployment - and with a
compose file inside a directory the walk could enter but not list, the build emitted its
`unlisted-directory` row AND an accusation whose remedy discards a correct `[[canonical]]`
block ("designates no file ... name one of them, or remove the `path`"). The accounting rows now
read the directories each component's walk could not list (`config["unlisted"]`): a block whose
`path` lies in one is said to be unknown, not absent; any other judgment for that component
names the directory first, as an unread file is named. A component with no such directory is
judged as before. Whether a refusal binds is measured, never assumed (`_listing`): root lists
through the mode bits, and the Linux gate runs unprivileged.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph.tests._estate import lay_down, producer, read, spawn  # noqa: E402
from tools.graph.tests._listing import allow_listing, deny_listing, lists  # noqa: E402

WEB = "services:\n  web:\n    build: .\n    environment:\n      TOPIC: t1\n"
API = "services:\n  api:\n    build: .\n"


def built_with_unlisted(tmp_path: Path, estate: dict, guarded: str):
    """The estate built with `guarded` (under the workspace) unlistable; (binds, the build)."""
    lay_down(tmp_path, estate)
    where = tmp_path / "ws" / guarded
    deny_listing(where)
    try:
        binds = not lists(where)
        proc = spawn(tmp_path)
    finally:
        allow_listing(where)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return binds, read(proc.returncode, tmp_path / "out")


def rows(built, kind: str) -> list[dict]:
    return [r for r in built.rows if r.get("kind") == kind]


def test_a_block_naming_a_file_in_an_unlisted_directory_is_unknown_not_accused(tmp_path):
    """The defect, in the row's own shape: a readable compose file beside the unlistable
    directory, so the block is judged - and it is said to be unknown, with the directory's
    remedy, never accused of designating nothing."""
    binds, built = built_with_unlisted(tmp_path, {
        "a": {"compose.yaml": WEB, "deploy/compose.yaml": WEB, "app.py": producer("TOPIC")},
        "__canonical__": [{"service": "web", "owner": "a", "path": "deploy/compose.yaml"}]},
        "a/deploy")
    said = [r["why"] for r in rows(built, "canonical-unmatched")]
    if binds:
        assert len(said) == 1, built.rows
        assert said[0].startswith("`deploy/compose.yaml` lies in `deploy`, which could not be "
                                  "listed (see its row), so whether it deploys `web` is unknown, "
                                  "not absent"), said
        assert "remove the `path`" not in said[0] and "name one of them" not in said[0], said
        assert "allow listing it, then rebuild" in said[0], said
    else:
        assert said == [], built.rows


def test_a_path_naming_the_unlisted_directory_itself_is_said_plainly(tmp_path):
    """Review B27-1: a block whose `path` IS the unlisted directory was told it "lies in"
    itself - said now as the directory that could not be listed."""
    binds, built = built_with_unlisted(tmp_path, {
        "a": {"compose.yaml": WEB, "deploy/compose.yaml": WEB, "app.py": producer("TOPIC")},
        "__canonical__": [{"service": "web", "owner": "a", "path": "deploy"}]},
        "a/deploy")
    said = [r["why"] for r in rows(built, "canonical-unmatched")]
    if binds:
        assert said == ["`deploy` could not be listed (see its row), so whether it deploys "
                        "`web` is unknown, not absent - allow listing it, then rebuild"], said
    assert not any("lies in `deploy`" in s for s in said), said


def test_a_sibling_sharing_the_directorys_prefix_is_not_held_by_it(tmp_path):
    """Review B27-5: `deployment/compose.yaml` is not in the unlisted `deploy` - the path
    boundary is kept, so a block naming it is judged on what was read (here: it designates a
    file the graph read, so no row)."""
    binds, built = built_with_unlisted(tmp_path, {
        "a": {"deployment/compose.yaml": WEB, "deploy/compose.yaml": API,
              "app.py": producer("TOPIC")},
        "__canonical__": [{"service": "web", "owner": "a", "path": "deployment/compose.yaml"}]},
        "a/deploy")
    assert rows(built, "canonical-unmatched") == [], (binds, built.rows)


def test_none_read_names_the_unlisted_directory_first_in_every_branch():
    """Review B27-5: `none_read`'s branches that `prefix` does not open - the host-map branch
    and the "read no deployment file" one - name the unlisted directory first, as the branch of
    unread files does; with no unlisted directory, each is `main`'s word for word."""
    from tools.graph.core.model import Node
    from tools.graph.links.canonical import accounting as A

    head = "HEAD."
    host = Node("cfg:a/chart/templates/cm.yaml", "cfg",
                {"component": "a", "kind": "configmap", "path": "chart/templates/cm.yaml",
                 "ships_in": ["chart"]})
    first = A.unlisted_spelling("deploy") + ", then: "
    for r in (A.Reading(host_maps=[host]), A.Reading()):
        plain = A.none_read(r, "a", head, mode=A.DESIGNATING)
        r.unlisted = ["deploy"]
        listed = A.none_read(r, "a", head, mode=A.DESIGNATING)
        assert plain.startswith(head) and listed == first + plain, (plain, listed)


def test_a_typo_beside_an_unlisted_directory_names_the_directory_first(tmp_path):
    """A block whose `path` is in no unlisted directory is still judged - a typo stays a typo -
    but the directory nobody could list is named first: what it holds may be the deployment."""
    binds, built = built_with_unlisted(tmp_path, {
        "a": {"compose.yaml": WEB, "deploy/compose.yaml": API, "app.py": producer("TOPIC")},
        "__canonical__": [{"service": "web", "owner": "a", "path": "compos.yaml"}]},
        "a/deploy")
    said = [r["why"] for r in rows(built, "canonical-unmatched")]
    assert len(said) == 1, built.rows
    if binds:
        assert said[0].startswith("`deploy` could not be listed (see its row), so what it declares "
                                  "is unknown - allow listing it, then rebuild, then: "), said
    else:
        assert said[0].startswith("`compos.yaml` designates no file"), said
    assert "`compos.yaml` designates no file of `a` deploying `web`" in said[0], said


def test_an_apart_block_over_an_unlisted_directory_names_it_first(tmp_path):
    """`apart-unmatched` goes through the same gate and sentence: a block refusing a service the
    graph did not read, in a component holding a directory nobody could list, names the
    directory before saying what was read."""
    binds, built = built_with_unlisted(tmp_path, {
        "a": {"compose.yaml": WEB, "deploy/compose.yaml": API, "app.py": producer("TOPIC")},
        "__apart__": [{"service": "redis", "component": "a", "note": "not ours"}]},
        "a/deploy")
    said = [r["why"] for r in rows(built, "apart-unmatched")]
    assert len(said) == 1, built.rows
    named = "`deploy` could not be listed (see its row), so what it declares is unknown"
    assert (named in said[0]) is binds, said


def test_an_unparsable_file_beside_an_unlisted_directory_names_the_directory_first(tmp_path):
    """The branch that names only unread files - the component's one compose file could not be
    parsed - names the unlisted directory before them too, never "remove the block" alone."""
    binds, built = built_with_unlisted(tmp_path, {
        "a": {"compose.yaml": "services: [\n", "deploy/compose.yaml": WEB,
              "app.py": producer("TOPIC")},
        "__apart__": [{"service": "redis", "component": "a", "note": "not ours"}]},
        "a/deploy")
    said = [r["why"] for r in rows(built, "apart-unmatched")]
    assert len(said) == 1, built.rows
    assert said[0].startswith("`deploy` could not be listed (see its row), so what it "
                              "declares is unknown - allow listing it, then rebuild, "
                              "then: ") is binds, said


def test_a_component_with_no_unlisted_directory_is_judged_as_before(tmp_path):
    """Bounded by exactly the unknown it covers (B15b-41): with everything listable, the typo's
    row is `main`'s, word for word in its head, and names no directory."""
    lay_down(tmp_path, {
        "a": {"compose.yaml": WEB, "deploy/compose.yaml": API, "app.py": producer("TOPIC")},
        "__canonical__": [{"service": "web", "owner": "a", "path": "compos.yaml"}]})
    proc = spawn(tmp_path)
    built = read(proc.returncode, tmp_path / "out")
    said = [r["why"] for r in rows(built, "canonical-unmatched")]
    assert len(said) == 1 and said[0].startswith("`compos.yaml` designates no file"), said
    assert "could not be listed" not in said[0], said
