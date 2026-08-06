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

_DEFAULT_DETAIL = (
    "Output derived from scanning a workspace is evidence, not product: it carries that "
    "workspace's paths and names. Only its CONCLUSIONS belong in a document you write by hand."
)


def check_output_dir(
    out: Path,
    repo_root: Path | None = None,
    *,
    what: str = "output",
    detail: str = _DEFAULT_DETAIL,
) -> str | None:
    """Return a refusal message if this path must not be written to, else None.

    **An allowlist, deliberately.** The first version of this check was a denylist of
    directory names (`docs`, `.sdlc`, …), which let `--out .` drop a private workspace
    inventory straight into the repo root — a denylist of places you thought of can never
    cover the places you did not.

    The rule instead: inside this repo, output must be under the gitignored scratch dir;
    anywhere outside the repo is the developer's own disk and none of this tool's business.
    That is complete by construction rather than by enumeration.

    This is a refusal rather than a docstring because the rule has already been broken once:
    an earlier revision wrote into `docs/` by default, the reports were committed, they
    reached a remote, and removing them cost a four-pass history rewrite.
    """
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    try:
        rel = Path(out).resolve().relative_to(repo_root)
    except ValueError:
        return None                      # outside the repo entirely — not ours to police
    if SCRATCH_DIR in rel.parts:
        return None
    return (f"refusing to write to {out}: that is inside the harness repo but outside "
            f"{SCRATCH_DIR}/, so the {what} would be committable.\n"
            f"   {detail}")
