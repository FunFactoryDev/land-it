# REVIEW.md

Review rules for land-it itself. Most of its value is in saying no at the right moment, so its worst bugs are the
quiet ones: a gate that lets something through.

## What failure looks like here
- `bin/land-it` hooks and gates: anything that **fails open**. A crash, an unknown base, an unreadable diff or a
  missing record must block or report, never read as "nothing to check".
- `bin/land-it` trust: any path from "the agent can edit files" or "the agent can run `land-it …`" to "a config
  command runs" or "trust is recorded" without the user typing the trust command.
- `bin/land-it` state and stamp: a green mark, stamp or verdict that outlives the tree it was earned on.
- Paths and shell: a file name, eval id or config value that reaches a shell, or a write that leaves the repo or worktree.
- `skills/**`: instructions that let the agent claim a stage ran when it didn't, or skip asking where the README says it asks.
- `templates/workflows/**`: a status that can turn green without the review completing; unpinned actions.
- `README*.md`: it lies. Every guarantee it states must be enforced by the code or the hooks, and EN/RU must match.

## Severity
- **Important**: any of the above, or a regression in a test of the audit reproductions.
- **Nit**: at most 3, none while an Important one is open.
- **Pre-existing**: only with a reason that holds against the base.

## Always check
- Every new gate or check has a test that fails on the old code.
- Python 3.9 stdlib only; no new dependencies.
- CHANGELOG and both READMEs describe the change the same way.

## Evidence
Cite `file:line` and quote it. Name the bug class and list every instance.

## Summary
Start with the count. If nothing needs fixing, say "Nothing to fix." and stop.
