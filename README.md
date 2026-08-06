# Icon Studio

A Kiro Crew app that turns a list of icon parameters into a set of hand-authored
SVG icons — and proves each one at its real size before you ship it.

Icon generation usually fails at the concept stage, not the drawing stage: ask for
a "settings" icon and you get a gear. Icon Studio is built around avoiding that.
Every job runs through a designer agent that proposes three structurally different
metaphors, names and rejects the cliché, and keeps a ledger of metaphors already
spent so two icons in a set never resolve to the same silhouette.

## How it works

1. **Describe behaviours, not pictures** in the app's form — "waiting on a human
   to approve", not "clock". Set canvas, stroke, style, keyline.
2. The app creates a job, composes a brief, and dispatches the `icon-designer`
   agent in a **background chat slot**. You stay on the page.
3. The agent proposes metaphors (or draws straight away, your choice), writes SVGs
   into the job directory, and renders a **contact sheet**.
4. The contact sheet shows every icon at each target size, on light and dark, and
   the agent reads it back and fixes what it sees before finishing.

## Structure

```
app.json                  Manifest — declares the agent, skill, UI page, backend hook
agents/icon-designer.json The designer agent, materialized to ~/.kiro/agents/ on enable
skills/icon-craft/        The craft doctrine: grid, stroke, SVG hygiene, review checklist
backend/contact_sheet.py  Headless-Chrome contact sheet renderer (standalone, no gateway deps)
backend/store.py          Job state and brief composition
backend/routes.py         External-app routes under /api/apps/icon-studio
scripts/contact_sheet.py  CLI wrapper — this is the agent's PROVE step
ui/index.mjs              Parameter form + job list + inline contact sheets
```

Runtime data lives outside the repo, in `~/.kiro/crew/workspace/icon-studio/`:

```
state.json           Job records
icons/<job-id>/      Generated SVGs
proofs/<job-id>.png  Contact sheets (1x and @2x)
metaphor-ledger.md   What metaphors are spent, and what was rejected
```

## Contact sheet rendering

Rasterization uses headless Chrome, discovered in this order: `ICON_STUDIO_CHROME`,
the Playwright browser cache, a system `google-chrome`/`chromium`, then
`/Applications`. If none is found the app says so instead of shipping unverified
icons — install one with `npx playwright install chromium`.

Three quirks are handled in `backend/contact_sheet.py`, all verified empirically on
Chrome for Testing (macOS arm64, build 1208). Read the comments before changing the
flags:

- `--user-data-dir` hangs `--headless=new` indefinitely, for any value, including a
  reused directory. It is not passed; renders serialize on a lock file instead.
- `--virtual-time-budget` never returns for a static page.
- `--window-size` includes ~87px of window chrome, so a window sized to the content
  leaves the bottom of the sheet unpainted — the sheet looks complete and is not.
  The inset is measured at runtime via `window.innerHeight`, not hardcoded.

Page height is read back from the DOM rather than computed in Python. An earlier
version computed it and was wrong by 71px, which pushed the last row of icons onto
the light page background as light strokes — invisible, in the one artefact whose
job is to show you what the icons look like.

## Install

```bash
kirocrew app install /path/to/kc-app-icon-studio
kirocrew gateway restart          # agents and backend hooks load at boot
```

Then enable it in the App Store. For UI iteration: `kirocrew app dev icon-studio`
hot-swaps `ui/` in about a second; agent and backend changes need a reload.

## Testing the agent on its own

The agent is selectable in any chat session's agent picker as `icon-designer`
once the app is enabled, so you can brief it conversationally instead of through
the form. Note it has less tool reach there than a standalone agent: app agents
never inherit the global `mcp.json`, and `managedToolPolicy` keeps them away from
`spawn_run`, `cron_*`, and `task_run`.

## Tuning it

Edit `agents/icon-designer.json` for behaviour and voice, `skills/icon-craft/SKILL.md`
for craft rules. Push rules toward the skill: a bloated prompt gets skimmed, a
checklist gets checked.

Do **not** hand-edit the materialized copy at
`~/.kiro/agents/icon-studio--icon-designer.json`. On re-registration every key
already on disk wins over the shipped template, so one local tweak permanently
shadows this repo's version. Delete that file and re-register to recover.
