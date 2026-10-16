# Dependencies and Workbook Destination

## Consent-gated dependencies

Run `scripts/check_dependencies.py --json` at startup. It is read-only and reports missing CLIs/modules.

If anything is missing, present the list and ask one question with:

- **Install for me** — run the official dv-connect installation steps and `pip install -r
  scripts/requirements.txt`.
- **I will install manually** — print exact commands and stop until the user confirms completion.
- **Cancel** — end without installing or generating data.

Never infer installation consent from a `testdata:` request.

## Workbook destination

Resolve the current user's Windows Desktop known folder and propose a timestamped filename. Ask whether to
use that path or choose another folder before generating the workbook.

`discover_testdata_workbook.py` defaults to Desktop when `--output` is omitted, but the agent must still ask
the user first. The default exists for script safety/convenience, not to bypass confirmation.

## Repository process context

Before confirming the output path, ask whether plugin/custom-process repository context should be used.
See `repository-process-context.md`. This question is mandatory, but providing a repository is optional.

## Approval summary

Run `scripts/summarize_testdata_workbook.py` after validation. Show its short summary immediately before the
approval prompt: target environment, record counts per table, relationships, email-alias behavior, workbook
path, and the explicit statement that no records have been written yet.
