"""The gated pipeline's CLIs — item 31 onward.

Each phase and each gate is a command a human or a runner invokes, never a
library call buried in an agent's prompt: the gates are enforced by the CLIs,
which is what makes a runner swap safe (item 29) and a tampered ledger
detectable (item 32). The artifacts are plain files in the target repository's
`.sdlc/`, committed there, so the record travels with the code it describes.
"""
