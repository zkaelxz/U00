# U00

This repo's default branch is nearly empty on purpose. The real content
is on two other branches:

- **`baihe-subtitler`** — the actual app. If you were told to build a
  roadmap step ("build Step X"), check this branch out first — it has
  its own `CLAUDE.md` with the exact instructions and the roadmap
  fetch command.
- **`claude/baihe-subtitle-planning-95qyvq`** — the planning branch,
  holds `docs/baihe-roadmap.md` (every step, in build order, with exit
  conditions).

If you're implementing a step: `git checkout baihe-subtitler` (or
`git fetch origin baihe-subtitler` if you can't switch branches), then
read its `CLAUDE.md`. Don't search issues/PRs for a step name — every
step lives in the roadmap file above, not as a GitHub issue.
