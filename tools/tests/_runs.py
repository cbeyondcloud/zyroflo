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
