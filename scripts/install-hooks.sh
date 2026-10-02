#!/usr/bin/env bash
# Symlink the shared checks into this repo's .git/hooks.
#
# Symlink, not copy: one source of truth. A copied hook is a second copy that
# silently stops matching the shared one.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hooks="$root/.git/hooks"
mkdir -p "$hooks"
for h in pre-commit; do
  ln -sf "../../scripts/pre-commit" "$hooks/$h"
  chmod +x "$root/scripts/pre-commit" 2>/dev/null || true
  echo "linked $hooks/$h -> scripts/pre-commit"
done
echo "done. The hook runs scripts/check.sh (fast gates) on every commit."
