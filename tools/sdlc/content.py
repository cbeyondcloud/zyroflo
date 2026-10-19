"""Where the harness's lifecycle content is - in a checkout, or in an installed wheel (Z2).

The stage SOPs (`sdlc/`), the templates, schemas and review rubric
(`sdlc_templates/`) and the pinned-binaries manifest (`third_party/manifest.json`)
sit at the checkout root, beside `tools/` rather than in it. Run from a checkout,
that root is where they are, as it always was. Installed as a dependency, only
`tools` is on disk - so the wheel carries a copy of the three under
`tools/_content/`, laid out the same way (`pyproject.toml`, `force-include`).

`root()` answers which of the two this process is reading, so a reader joins
the same relative path in both cases: `root() / "sdlc" / "stages"`. The packaged
copy wins whenever it exists: installed, the folder beside `tools/` is
`site-packages`, where another package's `sdlc/` must never be mistaken for ours.

Here, in `tools/sdlc/`, rather than in `tools/`: the emitted ledger check
carries `tools.sdlc` modules only, and `stage` imports this one.
"""
from __future__ import annotations

from pathlib import Path

#: The checkout root, when this file runs from a checkout.
CHECKOUT = Path(__file__).resolve().parents[2]
#: The copy shipped inside the wheel (`tools/_content/`); absent in a checkout.
PACKAGED = Path(__file__).resolve().parents[1] / "_content"


def installed() -> bool:
    """Whether this harness runs from an installed wheel rather than a checkout."""
    return PACKAGED.is_dir()


def root() -> Path:
    """The folder holding `sdlc/`, `sdlc_templates/` and `third_party/manifest.json`."""
    return PACKAGED if installed() else CHECKOUT
