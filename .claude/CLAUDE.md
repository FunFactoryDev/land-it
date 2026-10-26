# land-it

A Claude Code plugin: `bin/land-it` (stdlib Python 3.9+, one file) does the deterministic work, `skills/` hold the
judgment, `hooks/hooks.json` wires the gates. Docs: README.md (EN) and README.ru.md (RU), kept in sync.

Checks: `python3 -m unittest discover tests` and `claude plugin validate . --strict` (see `.land-it/config.json`).
Commit only the files you changed, by name; other agents may be writing in this tree.

@../REVIEW.md
