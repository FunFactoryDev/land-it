---
name: eval
description: Turn past incidents and reverts into eval cases for the reviewer, run the reviewer against them, and keep a change to REVIEW.md, CLAUDE.md, the model or the effort only if recall rises (or noise drops at equal recall). Use when the user asks to add an incident or revert as a test case, to measure the reviewer, or to check whether a reviewer tweak helped.
argument-hint: "scan | add <revert/fix commit or PR> | run [--model M] [--effort E] [--judge M] | list"
allowed-tools: Bash(land-it *), Bash(git log*), Bash(git show*), Bash(git blame*), Bash(gh pr view*), Bash(gh pr diff*)
---

# land-it eval

Talk to the user in their language. If `land-it` isn't on PATH, call
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead. Cases live in `.land-it/evals/*.json` and are committed:
they are the repo's memory of what the reviewer missed. Results go to `.land-it/evals/history.jsonl`.

## add: an incident becomes a case

`land-it eval scan [--days N]` lists reverts on the base branch whose reverted commit isn't a case yet: the
backlog of incidents. Offer them to the user one by one.

The user points at a revert, a fix commit, a PR or an incident. Find **the change that introduced the bug**:
- a revert says `This reverts commit <sha>`: that sha is the bad change;
- a fix: `git blame` the lines the fix touched, at the fix's parent (`git blame <fix>^ -- <file>`), to find
  the commit that wrote them;
- a squash-merged PR: its merge commit on the base branch.

Confirm the bug with the user before saving. A case is only as good as the bug it records: the article's team
had experts confirm each one. Then:

```
land-it eval add --commit <bad sha> --file <path> --line <N or N-M> --summary "<the bug, one line>"
```

`--range A..B` for a change spanning several commits; run `add` again with the same commit to record a second
bug in it. Add a few **known-good** changes too (`--clean --commit <sha>`), so noise is measured, not just recall.

## run: score the reviewer

Each case costs one full review run (`claude -p "/code-review <effort> <range>"` in a throwaway worktree of
that commit, with today's CLAUDE.md, REVIEW.md and land-it config copied in). Tell the user how many cases
will run before starting a full set.

```
land-it eval run [--model M] [--effort low|medium|high|xhigh|max] [--only ID ...]
```

- **recall**: share of recorded bugs the review cited within 5 lines of the recorded location.
- **noise**: findings per known-good case.
- `--judge haiku` scores with an LLM judge instead: it reads each review and decides whether the recorded defect
  is reported (worded differently or cited elsewhere still counts) and how many distinct findings there are.
  It costs one small extra call per case and is more accurate; runs with different scorers aren't compared.
- The **verdict** compares with the previous full run: keep a reviewer change only when recall rises, or recall
  holds and noise drops. Revert it otherwise. Priority is catching real bugs first, fewer review rounds second.
- `--only` runs a subset for iterating and isn't recorded. A run where a case failed isn't compared.

## Tuning loop

1. A case is missed → read that review's output (`--only <id>`), work out what rule would have caught it,
   and add it at the source: a line in REVIEW.md (what failure looks like, an invariant to always check), CLAUDE.md,
   or a lint rule if a machine can check it.
2. `land-it eval run --only <id>` until it's caught, then a full `land-it eval run` to be sure nothing else got worse.
3. Commit the rule change together with the updated `history.jsonl`.
