#!/bin/bash
# MEMPALACE PRE-COMPACT HOOK — Emergency save before compaction
#
# Claude Code "PreCompact" hook. Fires RIGHT BEFORE the conversation
# gets compressed to free up context window space.
#
# This is the safety net. When compaction happens, the AI loses detailed
# context about what was discussed. This hook forces one final save of
# EVERYTHING before that happens.
#
# Unlike the save hook (which triggers every N exchanges), this ALWAYS
# blocks — because compaction is always worth saving before.
#
# === INSTALL ===
# Add to .claude/settings.local.json:
#
#   "hooks": {
#     "PreCompact": [{
#       "hooks": [{
#         "type": "command",
#         "command": "/absolute/path/to/mempal_precompact_hook.sh",
#         "timeout": 30
#       }]
#     }]
#   }
#
# For Codex CLI, add to .codex/hooks.json:
#
#   "PreCompact": [{
#     "type": "command",
#     "command": "/absolute/path/to/mempal_precompact_hook.sh",
#     "timeout": 30
#   }]
#
# === HOW IT WORKS ===
#
# Claude Code sends JSON on stdin with:
#   session_id — unique session identifier
#
# We always return decision: "block" with a reason telling the AI
# to save everything. After the AI saves, compaction proceeds normally.
#
# === MEMPALACE CLI ===
# This repo uses: mempalace mine <dir>
# or:            mempalace mine <dir> --mode convos
# Set MEMPAL_DIR below if you want the hook to auto-ingest before compaction.
# Leave blank to rely on the AI's own save instructions.

STATE_DIR="$HOME/.mempalace/hook_state"
mkdir -p "$STATE_DIR"

# Optional: set to the directory you want auto-ingested before compaction.
# Example: MEMPAL_DIR="$HOME/conversations"
# Leave empty to skip auto-ingest (AI handles saving via the block reason).
MEMPAL_DIR=""

# ── Silent mode / opt-out ──────────────────────────────────────────────
# Set MEMPALACE_HOOKS_AUTO_SAVE=false to disable auto-save blocking entirely.
if [ -n "$MEMPALACE_HOOKS_AUTO_SAVE" ]; then
    case "$MEMPALACE_HOOKS_AUTO_SAVE" in
        false|0|no) echo "{}"; exit 0 ;;
    esac
else
    CONFIG_FILE="$HOME/.mempalace/config.json"
    if [ -f "$CONFIG_FILE" ]; then
        AUTO_SAVE=$(python3 -c "
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
    print(str(cfg.get('hooks', {}).get('auto_save', True)).lower())
except Exception: print('true')
" "$CONFIG_FILE" 2>/dev/null)
        if [ "$AUTO_SAVE" = "false" ]; then
            echo "{}"
            exit 0
        fi
    fi
fi

# Read JSON input from stdin
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id','unknown'))" 2>/dev/null)

echo "[$(date '+%H:%M:%S')] PRE-COMPACT triggered for session $SESSION_ID" >> "$STATE_DIR/hook.log"

# Optional: run mempalace ingest synchronously so memories land before compaction
if [ -n "$MEMPAL_DIR" ] && [ -d "$MEMPAL_DIR" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_DIR="$(dirname "$SCRIPT_DIR")"
    python3 -m mempalace mine "$MEMPAL_DIR" >> "$STATE_DIR/hook.log" 2>&1
fi

# Block — compaction = save everything
cat << 'HOOKJSON'
{
  "decision": "block",
  "reason": "MemPalace emergency save — compaction imminent. Use mempalace_diary_write (thorough summary) and mempalace_add_drawer (ALL quotes, decisions, code, context) to save ALL content before context is lost. Do NOT use native auto-memory files."
}
HOOKJSON
