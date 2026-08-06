#!/usr/bin/env bash
# Removes links and generated proofs. Your icons, ledger, and job history are
# LEFT IN PLACE — they are your work, not app state.
set -euo pipefail

CREW_HOME="${KIROCREW_HOME:-$HOME/.kiro/crew}"
WS="$CREW_HOME/workspace/icon-studio"

# Only remove the symlink we created, never a real directory.
if [ -L "$CREW_HOME/skills/icon-craft" ]; then
  rm -f "$CREW_HOME/skills/icon-craft"
fi

rm -rf "$WS/proofs"

echo "icon-studio: removed skill link and rendered proofs."
echo "             kept your SVGs, ledger, and job history in $WS"
