---
name: sweep
description: Review one area of existing code for old bugs, the way the Claude Code team's nightly review agents do - today's high-risk area by rotation, or a path you name - and file the new findings as one GitHub issue. Use when the land-it sweep workflow runs it on a schedule, or when the user asks to hunt for bugs in existing code.
argument-hint: "[path] [--ci]"
allowed-tools: Bash(land-it *), Bash(git log*), Bash(git blame*), Bash(gh issue list*), Bash(gh issue create*), Bash(gh label create*)
---

# land-it sweep

Talk to the user in their language. If `land-it` isn't on PATH, call
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead. In Codex, use the path from land-it's note at the start of the session (its SessionStart hook), since Codex doesn't put the plugin's `bin/` on PATH.

Pre-PR review only sees what changes. Bugs in code nobody touches stay until they cause an incident; a sweep
reads one area at a time, so over a rotation every high-risk area gets read again.

## 1. Target

The path argument, or `land-it sweep-target`: it rotates by day over the repo's `risk.high` areas (or its
top-level source directories when none are configured).

## 2. Review

Run the `code-review` skill (Skill tool) with `<effort> <target>`, where effort is `review_effort.medium` from
`land-it risk` (default `medium`). A path target reviews the files as they are, not a diff. Keep **Important**
findings with `file:line` evidence; drop nits and style, since a sweep is for bugs. In Codex, or when the skill
is unavailable: `land-it review --path <target> --runner codex|claude` prints the findings as checked JSON.

## 3. Drop what's already filed

`gh issue list --label land-it-sweep --state open --json number,title,body --limit 100`. Skip any finding
already listed in an open sweep issue (same file and defect).

## 4. File one issue

No new findings: say "Nothing new in <target>" and stop.

Otherwise one issue for the sweep:
- `gh label create land-it-sweep --color B60205 --description "Found by land-it sweep" --force`
- `gh issue create --label land-it-sweep --title "land-it sweep: <target> — <N> finding(s)" --body "<body>"`

Body: one checklist line per finding, `- [ ] \`file:line\` <summary>`, the evidence quoted under it, then
`Found by /land-it:sweep on <date> at review effort <effort>.` When one of these turns out to be a real bug
that shipped, it's also an eval case: `/land-it:eval add`.

With `--ci` (the scheduled workflow), file it without asking. Otherwise show the draft and file it on the user's yes.
