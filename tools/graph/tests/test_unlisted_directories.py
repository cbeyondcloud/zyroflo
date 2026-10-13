"""T611, the build's half: a directory the activation walk could enter but not list is said.

Its files reached no extractor, and nothing recorded it - could-not-look read as absence, the
rule `repo_walk` exists to keep. Batch 18's core change names such a directory (`find_files`'
`unlisted`); here the build records it as a row of the activation's own, in the shard, and
stamps it poisoned, as an unreadable file is: content nobody could list is content nobody may
claim unchanged. Whether a refusal binds is measured, never assumed (`_listing`): root lists
through the mode bits, and the Linux gate runs unprivileged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness  # noqa: E402
from tools.graph.core.model import check_unresolved  # noqa: E402
from tools.graph.tests._estate import lay_down, read, spawn  # noqa: E402
from tools.graph.tests._listing import allow_listing, deny_listing, lists  # noqa: E402

EACCES = "PermissionError (EACCES)"
ESTATE = {"a": {"app.py": "import os\nA = os.environ['A']\n",
                "sub/more.py": "import os\nB = os.environ['B']\n"}}


def unlisted(rows: list[dict]) -> list[tuple[str, str, str]]:
    return sorted((r["component"], r["source_file"], r["error"]) for r in rows
                  if r.get("kind") == "unlisted-directory")


def cites(built, rel: str) -> bool:
    """Whether anything the build produced - a fact or a row - came from the file `rel`."""
    return any(rel in json.dumps(record) for record in built.nodes + built.edges + built.rows)


def test_a_directory_the_build_cannot_list_is_a_row_and_its_remedy_is_accepted(tmp_path):
    """The row names the directory and its cause, at exit 0 - a gap admitted, as an off-case
    file's is - and following its remedy (allow listing, rebuild) reads what it held."""
    lay_down(tmp_path, ESTATE)
    guarded = tmp_path / "ws" / "a" / "sub"
    deny_listing(guarded)
    try:
        binds = not lists(guarded)
        first = spawn(tmp_path)
    finally:
        allow_listing(guarded)
    assert first.returncode == 0, first.stdout + first.stderr
    built = read(first.returncode, tmp_path / "out")
    if binds:
        assert unlisted(built.rows) == [("a", "sub", EACCES)], built.rows
        assert not cites(built, "sub/more.py")
    else:
        assert unlisted(built.rows) == [] and cites(built, "sub/more.py")
    again = spawn(tmp_path)
    assert again.returncode == 0, again.stdout + again.stderr
    rebuilt = read(again.returncode, tmp_path / "out")
    assert unlisted(rebuilt.rows) == [] and cites(rebuilt, "sub/more.py")


def test_a_component_holding_an_unlisted_directory_is_never_called_current(tmp_path):
    """Its stamp entry is poisoned, so `--check-fresh` answers stale, naming why, until the
    directory can be listed - and once it can and the snapshot is re-published, current."""
    lay_down(tmp_path, ESTATE)
    guarded = tmp_path / "ws" / "a" / "sub"
    deny_listing(guarded)
    try:
        binds = not lists(guarded)
        published = spawn(tmp_path, "--publish-snapshot")
        check = spawn(tmp_path, "--check-fresh")
    finally:
        allow_listing(guarded)
    assert published.returncode == 0, published.stdout + published.stderr
    shard = tmp_path / "ws" / "a" / ".sdlc" / "graph" / "shard.unresolved.jsonl"
    rows = [json.loads(line) for line in shard.read_text(encoding="utf-8").split("\n") if line]
    assert unlisted(rows) == ([("a", "sub", EACCES)] if binds else []), rows
    if binds:
        assert check.returncode == 2, check.stdout + check.stderr
        assert (f"freshness: STALE a - {freshness.UNLISTED_WORDS}" in check.stdout), check.stdout
    else:
        assert check.returncode == 0, check.stdout + check.stderr
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    current = spawn(tmp_path, "--check-fresh")
    assert current.returncode == 0 and "current" in current.stdout, current.stdout


def test_an_unlisted_directory_under_a_declared_child_is_the_childs(tmp_path):
    """As an off-case file is (T591): a directory under a declared child is that child's, so
    the parent's walk does not report it a second time."""
    estate = {".": {"app.py": "import os\nA = os.environ['A']\n"},
              "svc": {"main.py": "import os\nB = os.environ['B']\n",
                      "sub/more.py": "import os\nC = os.environ['C']\n"},
              "__components__": [("root", "."), ("svc", "svc")]}
    lay_down(tmp_path, estate)
    guarded = tmp_path / "ws" / "svc" / "sub"
    deny_listing(guarded)
    try:
        binds = not lists(guarded)
        done = spawn(tmp_path)
    finally:
        allow_listing(guarded)
    assert done.returncode == 0, done.stdout + done.stderr
    rows = read(done.returncode, tmp_path / "out").rows
    assert unlisted(rows) == ([("svc", "sub", EACCES)] if binds else []), rows


def test_the_row_names_the_directory_its_cause_and_what_lifts_it():
    """Every platform: the row is the activation's, passes its contract, and says what to
    do - a remedy a person can follow (allow listing it, then rebuild)."""
    row = B.unlisted_row("a", "sub", EACCES)
    assert (row["extractor"], row["kind"], row["source_file"], row["error"]) == (
        B.ACTIVATION, "unlisted-directory", "sub", EACCES)
    assert "`sub` could not be listed" in row["why"] and "Allow listing it, then rebuild" \
        in row["why"]
    assert check_unresolved([row], B.UNRESOLVED_FIELDS, B.ACTIVATION) == []
    causeless = {k: v for k, v in row.items() if k != "error"}
    assert check_unresolved([causeless], B.UNRESOLVED_FIELDS, B.ACTIVATION), \
        "the kind promises its cause, and the contract holds it to that"
    assert "the component's own directory" in B.unlisted_row("a", ".", EACCES)["why"]


def test_an_unlisted_directory_is_stamped_as_content_nobody_could_read(tmp_path):
    """Every platform: the record has a stamp entry of its own, poisoned as an unreadable
    file's digest is, so `stamp_matches` never accepts it; and `--check-fresh` words it in a
    clause of its own (D15b-123)."""
    act = B.Activation(unlisted={"a": [("sub", EACCES)]})
    digests = B.input_digests(act, [{"name": "a", "path": "a"}], tmp_path)
    entry = digests["a"][freshness.ACTIVATION_UNLISTED]
    assert entry.startswith("unreadable-") and not freshness.stamp_matches(entry, entry)
    assert freshness._moved_words([freshness.ACTIVATION_UNLISTED], {}) == \
        freshness.UNLISTED_WORDS
