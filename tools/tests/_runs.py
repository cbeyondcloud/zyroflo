"""Fixture support: a run that has actually done its phase's work.

Item 36 made a gate stand on the artifacts its phase owed, and that turned a
quiet assumption in this suite into 57 red tests: every fixture here parked a
run at a phase and approved it *without the phase having produced anything*.
The tests were right about what they were testing and wrong about the world
they tested it in — which is the same shape as the defect, seen from the other
side.

`deliver` is the honest fixture. It asks the stage contract what the phase
owes, writes it, and records it, so a test about ledger tampering is about
ledger tampering rather than about an empty run folder.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402


def deliver(target: Path, run_id: str, phase: str) -> list[str]:
    """Write and record everything `phase` owes. Returns what it delivered."""
    folder = ledger.run_dir(target, run_id)
    written: list[str] = []
    for rel in stages.by_phase(phase).produces:
        path = folder / rel.rstrip("/")
        if rel.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            (path / "component.md").write_text(
                f"{phase} did its work\n", encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{phase} did its work\n", encoding="utf-8")
        ledger.record_artifact(target, run_id, rel)
        written.append(rel)
    return written


def park(target: Path, run_id: str, phase: str, status: str = "awaiting_approval") -> dict:
    """Deliver the phase's artifacts, then move the run to it — the order a
    real run goes in, and the only order that survives the contract."""
    deliver(target, run_id, phase)
    return ledger.advance(target, run_id, phase=phase, status=status)


def sca_clean(target: Path, run_id: str, components: list[str] | None = None) -> Path:
    """A recorded SCA gate that ran and found nothing.

    Item 40 made an SCA result something P5 owes once the engine has run, so a
    fixture asserting "this run owes nothing" has to have run the gate. Built
    through `sca._document` rather than hand-rolled: a fixture that spells the
    shape itself passes while the real one changes underneath it, which is the
    seam item 29 found and item 39 was written against.
    """
    from tools.review import sca as scanner

    named = [{"name": n} for n in (components or ["all"])]
    doc = scanner._document(target, [{"name": scanner.TOOL, "status": "ok",
                                      "detail": "", "findings": 0,
                                      "components": [c["name"] for c in named]}],
                            [], named)
    path = ledger.run_dir(target, run_id) / "50-sca.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    ledger.record_artifact(target, run_id, "50-sca.json")
    return path
