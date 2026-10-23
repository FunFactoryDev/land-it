# REVIEW.md

Review rules for this repo. `/code-review` reads them through the `@REVIEW.md` line in CLAUDE.md;
Code Review on GitHub reads this file directly. Keep it short: every line costs every review.

## What failure looks like here
<!-- One line per area: what "broken" means there. Replace the examples. -->
- `src/api/**`: it returns wrong data, or a 500 where a 4xx belongs.
- `docs/**`: it lies, i.e. documents behavior the code doesn't have.
- `config/**`: it misroutes: traffic, jobs or secrets go to the wrong place.

## Severity
- **Important**: would break production, lose data, open a hole, or mislead a user. Fix before merge.
- **Nit**: style or taste. At most 3 per review, and none at all when there are Important findings.
- **Pre-existing**: the bug was there before this change. Report it, never as the author's.

## Skip
- Generated, vendored and lock files: `dist/**`, `vendor/**`, `**/*.lock`
- Formatting: the formatter owns it.

## Always check
<!-- Repo-specific invariants, e.g. "every DB write goes through repo/", "no network in unit tests". -->

## Evidence
Every finding cites `file:line` and quotes the line. A guess from a function name is not a finding.
When one instance of a bug class is found, name the class and list every instance.

## Summary
Start with the count: "2 Important, 1 Nit." If nothing needs fixing, say "Nothing to fix." and stop.
