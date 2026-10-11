# Session harvest

Findings that only ever existed in a cloud session's chat, not in a PR body, issue or doc.
Kept in the repo so they outlive the account and the sessions that produced them.

Why: a session's final summary is the only part of its chat the lead can read (the
`post_turn_summary` of `list_sessions`). Full transcripts are visible only in the app's
session view. Everything a session leaves undone therefore has to be written into its PR
body under "Found but not fixed"; this folder is the backstop for the sessions that did not.

| File | What it is |
|---|---|
| `harvest-prompt.md` | Paste-ready prompt that collects every session's final summary on an account and flags the ones that mention something left undone. Run it on any account that worked on this repo. |
| `session-summaries-2026-09-28-to-10-03.md` | Final summaries of the 97 sessions from the first week (history starts 2026-09-28). 15 flagged. |
| `session-summaries-2026-10-04-to-10-10.md` | Final summaries of the 583 sessions from 10/04 to 10/10. 200 flagged. |
| `triage-2026-10-10.md` | Every flagged note checked against the code at c67ad646, PR bodies, issues and the fix queue: done, covered, still open, owner decision, too vague. |
| `open-pr-findings-2026-10-10.md` | "Found but not fixed", "unsure" and failing-test notes harvested from the bodies, comments and reviews of the open PRs. |
| `merged-pr-findings-2026-10-10.md` | The same harvest over the 61 PRs merged 10/04 to 10/10. |

Each new harvest run appends a dated pair (summaries + triage). The lead's hourly loop
reads every finished session's summary before archiving it and adds anything missing to
the fix queue, so this folder should only grow by the daily full pass.
