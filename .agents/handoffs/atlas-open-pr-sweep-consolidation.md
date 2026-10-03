# Atlas handoff: reviewed open-PR sweep consolidation

- Owner: Atlas, with Harbor owning queue and CI operation after the PR opens.
- Branch/worktree: `harbor/open-pr-sweep-consolidation` in
  `/private/tmp/rapid-mlx-open-pr-sweep-consolidation`.
- Intention: drain nine independently reviewed internal PRs through one CI and
  merge-queue candidate while preserving every exact reviewed head in history.
- Scope: PRs #3972, #3290, #3987, #3950, #3569, #3962, #3963, #3587, and
  #3973. The community-authored warning-policy PR #3988 remains separate so its
  contribution record is preserved.
- Non-goals: no CUA implementation changes, no new feature work, no release,
  and no behavior beyond the source PR contracts.

## Verified facts

- Every source PR exact head is an ancestor of the consolidation head.
- The nine source diffs have zero overlapping paths and merged without a
  conflict. The resulting source union changes 232 paths; this handoff is the
  consolidation adds this handoff and the singleton queue contract correction
  required by the already-merged #4055 configuration.
- `git diff --check` and Ruff check/format pass for all 38 changed Python files.
- Focused Python verification passes: 84 GLM capture/contract tests; 3,729
  telemetry, memory-gate, alias, and LTX tests; and 340 drafter/runtime/CI tests
  with one expected platform skip under the repository-pinned `mlx-vlm 0.7.2`.
- The model-unload Swift suites pass 13 tests across two suites.
- A comparison run with a stale host-level `mlx-vlm 0.7.1` produced the
  expected vendored-source parity failure. Re-running with the repository pin
  passed, so this is host dependency drift rather than an integration defect.
- The only CUA-named paths are two stale research documents deleted by #3963;
  no CUA implementation is added or changed.

## Queue and next action

PR #4055 put the managed merge queue into singleton mode. PR #3988 is already
running as the current candidate. Push and open the consolidation PR without a
queue label, let its head-bound CI finish, then authorize and enqueue that exact
head after #3988 lands. Once the consolidation merges, close or verify automatic
closure of the nine source PRs and leave the later feature cohort deferred.

The dedicated role-messaging channel was unavailable in this shell. This file
records the required start/completion FYI for Pixel, Vector, Harbor, Echo, and
ds0731 until that channel is restored.
