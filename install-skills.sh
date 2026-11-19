#!/bin/bash
# install-skills.sh [repo-dir ...] — copy the skills under each repo's skills/ folder into Claude's and Codex's skill
# folders. Text only: SKILL.md files, no hooks, no binaries. Default: this repo.
set -euo pipefail
dirs=("${@:-$(cd "$(dirname "$0")" && pwd)}")
for repo in "${dirs[@]}"; do
  [ -d "$repo/skills" ] || { echo "no skills/ in $repo"; continue; }
  for s in "$repo"/skills/*/; do
    name=$(basename "$s"); [ -f "$s/SKILL.md" ] || continue
    mkdir -p "$HOME/.claude/skills/$name" && cp "$s/SKILL.md" "$HOME/.claude/skills/$name/SKILL.md"
    # Other agents never get claude-only rules or private paths: drop any line that names them.
    mkdir -p "$HOME/.codex/skills/$name" && grep -viE 'claude-only|private-notes' "$s/SKILL.md" > "$HOME/.codex/skills/$name/SKILL.md"
    echo "installed $name (Claude + Codex)"
  done
done
