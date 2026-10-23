---
name: trust
description: Approve the commands in .claude/land-it.json so land-it's hooks and checks may run them. The user types it; Claude never invokes it.
disable-model-invocation: true
---

# land-it trust

Talk to the user in their language.

Typing this command is the approval: land-it's `UserPromptExpansion` hook recorded it before you read this,
because that hook fires only for commands the user types. Look for the land-it context listing the trusted
commands:

- **Present**: show the user that list, one line per command, and say that any later change to `checks` or
  `on_edit` needs `/land-it:trust` again. Changes to other keys (risk, rounds, effort) don't.
- **Absent**: say plainly that trust was **not** recorded (the plugin's hooks may be disabled, or the repo has
  no `.claude/land-it.json`). Don't record it any other way: running `land-it trust` from your shell is refused
  by design. The user can run it from their own terminal.
