"""Where a tool in this repo is allowed to write.

One rule, one implementation, shared by every tool that produces a file from scanning a
developer's workspace. It lives here rather than inside `core/` because it is a fact about
*this* repository's hygiene, not about the graph meta-model — and it lives in its own module
rather than inside any one tool because a second copy of an allowlist is how allowlists rot
into denylists.
"""
from __future__ import annotations

from pathlib import Path

#: The one gitignored directory inside this repo that scan output may land in.
SCRATCH_DIR = ".tmp"

#: The directory inside a target repository that holds THAT repository's own artifacts.
#: Unlike the scratch dir this one is meant to be COMMITTED — it is how the next person to
#: clone that repository inherits its profile, its plans and its graph baseline.
OWN_ARTIFACT_DIR = ".sdlc"

_DEFAULT_DETAIL = (
    "Output derived from scanning a workspace is evidence, not product: it carries that "
    "workspace's paths and names. Only its CONCLUSIONS belong in a document you write by hand."
)


def enclosing_repo(path: Path) -> Path | None:
    """The git repository containing `path`, or None if it is inside none.

    `path` need not exist: an output directory is created after this check runs, so the
    walk tests each ancestor rather than requiring the leaf to be there.
    """
    here = Path(path).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def check_output_dir(
    out: Path,
    repo_root: Path | None = None,
    *,
    own_repo: Path | None = None,
    what: str = "output",
    detail: str = _DEFAULT_DETAIL,
) -> str | None:
    """Return a refusal message if this path must not be written to, else None.

    **An allowlist, deliberately.** The first version of this check was a denylist of
    directory names (`docs`, `.sdlc`, …), which let `--out .` drop a private workspace
    inventory straight into the repo root — a denylist of places you thought of can never
    cover the places you did not.

    **The rule, in one sentence: a repository may carry its own facts and nothing else.**
    That is Rev 12's sharpening, and it is a sharpening rather than a relaxation. The
    original rule was "nothing committable", which was right when every artifact belonged
    in this repo and wrong the moment a target repository became the designated home for
    its own graph. Three cases follow from it:

    * `own_repo`'s own `.sdlc/` — the one place a repository's own artifacts belong, and
      the one place they are *meant* to be committed. `own_repo` is the repository that
      COMMITS this output: for a zero-config build the single repository the graph describes
      (`inferred_from`), and for an estate install the target hub, passed as `build.py --home`.
      build.py checks only that the home is a repository ROOT — not that it is a fitting hub —
      because a subdirectory would place the graph under the enclosing repository, a tree it
      is not about; the check below then permits only that repository's own `.sdlc/`. A
      spanning graph names no owner on its own, so absent such a declaration no repository may
      carry it.
    * inside this repo — allowed only under the gitignored scratch dir, unchanged.
    * inside any *other* repository — refused. This is the half the old rule could not see:
      it declared everything outside this repo "the developer's own disk and none of our
      business", which stops being true once output is written into repositories at all.
      A workspace-spanning graph committed into one component would carry every sibling's
      names into a tree that may not be as private as the workspace is.

    Output landing in no repository at all is still nobody's business — it cannot be
    committed by anyone.

    This is a refusal rather than a docstring because the rule has already been broken once:
    an earlier revision wrote into `docs/` by default, the reports were committed, they
    reached a remote, and removing them cost a four-pass history rewrite.
    """
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    resolved = Path(out).resolve()

    # 1. A repository carrying its OWN facts, in the directory designated for them.
    if own_repo is not None:
        own = Path(own_repo).resolve()
        if _within(resolved, own / OWN_ARTIFACT_DIR):
            return None
    else:
        own = None

    # 2. Inside this repo. Checked before any walk-up so that `repo_root`, when a caller
    #    passes one, is authoritative about what "this repo" means — a test fixture is a
    #    directory, not a clone, and asking git about it would answer a different question.
    if _within(resolved, repo_root):
        if SCRATCH_DIR in resolved.relative_to(repo_root).parts:
            return None
        return (f"refusing to write to {out}: that is inside the harness repo but outside "
                f"{SCRATCH_DIR}/, so the {what} would be committable.\n"
                f"   {detail}")

    # 3. Outside it: refuse only where the path would land in some repository's tree, and
    #    say which of the two mistakes it is — wrong directory, or wrong repository.
    holder = enclosing_repo(resolved)
    if holder is None:
        return None
    if own is not None and holder == own:
        return (f"refusing to write to {out}: a repository carries its own artifacts under "
                f"{OWN_ARTIFACT_DIR}/, and this path is outside it.\n"
                f"   Write to {own / OWN_ARTIFACT_DIR}/graph, which is the committed home for "
                f"this repository's own {what}, or to somewhere outside the repository.")
    return (f"refusing to write to {out}: that is inside {holder}, a repository this {what} "
            f"is not about, so it would be committed into a tree it does not describe.\n"
            f"   {detail}")
