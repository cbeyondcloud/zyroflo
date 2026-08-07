# Runbook

A component matched by **no extractor at all**, and that is its job.

The pipeline distinguishes "nothing matched this component" from "this component has no
couplings", and only the second is a legitimate answer. A docs-only component is named in
the report and the build still exits 0: a coverage hole is a finding, not a failure.

## Restarting the worker

1. Drain the queue.
2. Restart the worker.
3. Confirm the table is advancing.
