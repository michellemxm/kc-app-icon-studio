#!/usr/bin/env bash
# Optional accelerator. The app works without this: the backend calls
# ensure_workspace() on route registration, which does the same thing.
set -euo pipefail

CREW_HOME="${KIROCREW_HOME:-$HOME/.kiro/crew}"
WS="$CREW_HOME/workspace/icon-studio"
APP="$CREW_HOME/apps/icon-studio"

mkdir -p "$WS/icons" "$WS/proofs"

[ -f "$WS/state.json" ] || printf '{\n  "jobs": []\n}\n' > "$WS/state.json"

if [ ! -f "$WS/metaphor-ledger.md" ]; then
  cat > "$WS/metaphor-ledger.md" <<'LEDGER'
# Metaphor ledger

One row per shipped icon. The icon-designer agent reads this before designing so
it never reuses a spent metaphor or ships two icons with the same silhouette.

| Icon | Metaphor used | Rejected | Notes |
| --- | --- | --- | --- |
LEDGER
fi

# Flat skill link: the skill scanner only looks at ~/.kiro/crew/skills/<name>/SKILL.md.
if [ -d "$APP/skills/icon-craft" ] && [ ! -e "$CREW_HOME/skills/icon-craft" ]; then
  mkdir -p "$CREW_HOME/skills"
  ln -sfn "$APP/skills/icon-craft" "$CREW_HOME/skills/icon-craft"
fi

chmod +x "$APP/scripts/contact_sheet.py" 2>/dev/null || true

if ! command -v google-chrome >/dev/null 2>&1 \
   && [ ! -d "$HOME/Library/Caches/ms-playwright" ] \
   && [ ! -d "$HOME/.cache/ms-playwright" ] \
   && [ ! -d "/Applications/Google Chrome.app" ]; then
  echo "icon-studio: no Chrome/Chromium found — contact sheets will fail."
  echo "             install one with: npx playwright install chromium"
fi

echo "icon-studio: workspace ready at $WS"
