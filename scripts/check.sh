#!/usr/bin/env bash
# check.sh — the ONE gate script. Same file for the pre-commit hook and for CI.
#
# Why one script: a green local run must mean a green CI run. Two copies of the
# commands drift, and the drift shows up as a CI failure nobody can reproduce.
#
# Usage:
#   scripts/check.sh            fast: lint + fast tests + dependency audit
#   scripts/check.sh --full     adds the slow gates (CI on PR, or nightly)
#   scripts/check.sh --lint     one gate only, for iterating
#
# Design rules (from the CI skill, kept because each was earned):
#   * A missing tool is SKIPPED, not failed. A hook that blocks a commit because
#     ruff is absent from one interpreter gets disabled within a day.
#   * Nothing here WRITES. A gate that regenerates its own input reports on the
#     evidence it just rewrote.
#   * Every gate must be able to fail. An empty gate is a green tick that trains
#     the reader to ignore the tick.

set -uo pipefail

FULL=0
ONLY=""
for a in "$@"; do
  case "$a" in
    --full) FULL=1 ;;
    --lint) ONLY=lint ;;
    --test) ONLY=test ;;
    --audit) ONLY=audit ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "check.sh: unknown option $a" >&2; exit 2 ;;
  esac
done

# Resolve the repo root: this script is symlinked into .git/hooks, so $0's
# directory is NOT the repo when run as a hook.
if root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$root" || exit 2
else
  cd "$(dirname "$0")/.." || exit 2
fi

PY="${PYTHON:-python3}"

# Per-project declaration of what to run. Sourced before the gates so a project
# states its own test split rather than the shared script guessing.
if [ -f project.conf ]; then
  # shellcheck disable=SC1091
  . ./project.conf
fi

FAILED=()
SKIPPED=()

hr()   { printf '\n\033[1m== %s\033[0m\n' "$1"; }
pass() { printf '   \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '   \033[31mFAIL\033[0m %s\n' "$1"; FAILED+=("$1"); }
skip() { printf '   \033[33mSKIP\033[0m %s\n' "$1"; SKIPPED+=("$1"); }

# A tool may be a console script OR a module in this interpreter. Test both.
have() { command -v "$1" >/dev/null 2>&1; }
have_mod() { "$PY" -c "import $1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- lint
if [ -z "$ONLY" ] || [ "$ONLY" = lint ]; then
  hr "lint"
  # Rule selection is PINNED and explicit. ruff's default rule set has grown a
  # lot of style opinion (the current default flagged 488 items on a real repo,
  # almost all cosmetic). We want only things that are WRONG:
  #   E4/E7/E9 pycodestyle errors, E9 syntax errors, F pyflakes.
  RUFF_SELECT="E4,E7,E9,F"
  BASELINE=".ruff-baseline"

  # BASELINE. A repo with no lint history is not going to be made clean in the
  # commit that adds the gate, and a gate that starts red gets disabled within a
  # day. So we fail on NEW findings only. This keeps the gate able to fail —
  # which is the whole point — without blocking on legacy debt.
  #
  # Regenerate deliberately, never automatically:  ruff ... > .ruff-baseline
  ruff_run() {
    if have ruff; then ruff check --select "$RUFF_SELECT" --output-format=concise --exclude .venv .
    else "$PY" -m ruff check --select "$RUFF_SELECT" --output-format=concise --exclude .venv .; fi
  }
  if have ruff || have_mod ruff; then
    current="$(ruff_run 2>/dev/null | grep -oE '^[^:]+:[0-9]+:[0-9]+: [A-Z]+[0-9]+' | sort -u)"
    n_current=$(printf '%s\n' "$current" | grep -c . || true)
    if [ -f "$BASELINE" ]; then
      new_findings="$(comm -23 <(printf '%s\n' "$current") <(sort -u "$BASELINE"))"
      n_new=$(printf '%s\n' "$new_findings" | grep -c . || true)
      if [ "$n_new" -gt 0 ]; then
        printf '%s\n' "$new_findings" | head -20
        fail "ruff: $n_new NEW finding(s) (baseline has $(grep -c . "$BASELINE"))"
      else
        pass "ruff: no new findings ($n_current total, baseline $(grep -c . "$BASELINE"))"
      fi
      # fixed items are worth knowing about: the baseline can shrink
      fixed=$(grep -c . "$BASELINE" || true)
      if [ "$n_current" -lt "$fixed" ]; then
        printf '   note: %s finding(s) fixed since the baseline — you can regenerate it\n' \
          "$((fixed - n_current))"
      fi
    else
      fail "ruff: no $BASELINE file — generate it once (see README: 'Baseline the lint gate')"
    fi
  else
    skip "ruff not available to $PY"
  fi
fi

# ---------------------------------------------------------------- test
if [ -z "$ONLY" ] || [ "$ONLY" = test ]; then
  hr "test (fast)"
  # FAST_TESTS is a space-separated list of test paths that must finish quickly.
  # Slow suites belong in --full; a gate that always busts its budget gets
  # disabled, which is worse than not having it.
  # Two shapes of test are supported, because real repos have both:
  #   FAST_TESTS   pytest targets
  #   FAST_SCRIPTS standalone scripts that exit non-zero on failure (many repos
  #                predate pytest and use module-level asserts; forcing them into
  #                pytest collects zero tests and passes vacuously)
  ran=0
  if [ -n "${FAST_SCRIPTS:-}" ]; then
    for s in $FAST_SCRIPTS; do
      ran=1
      if out="$("$PY" "$s" 2>&1)"; then
        pass "script $s"
      else
        printf '%s\n' "$out" | tail -15
        fail "script $s"
      fi
    done
  fi
  if [ -n "${FAST_TESTS:-}" ]; then
    ran=1
    if have pytest || have_mod pytest; then
      if "$PY" -m pytest $FAST_TESTS -q; then pass "fast suite"; else fail "fast suite"; fi
    else
      skip "pytest not available (FAST_TESTS declared)"
    fi
  fi
  [ "$ran" = 0 ] && skip "no FAST_TESTS or FAST_SCRIPTS declared for this repo"
fi

# ---------------------------------------------------------------- audit
if [ -z "$ONLY" ] || [ "$ONLY" = audit ]; then
  hr "dependency audit"
  if [ -f requirements.txt ] || [ -f requirements-dev.txt ]; then
    if have pip-audit || have_mod pip_audit; then
      if "$PY" -m pip_audit -r requirements-dev.txt 2>/dev/null \
         || "$PY" -m pip_audit -r requirements.txt; then
        pass "pip-audit"
      else
        fail "pip-audit found advisories"
      fi
    else
      skip "pip-audit not available"
    fi
  else
    skip "no requirements file to audit"
  fi
fi

# ---------------------------------------------------------------- full-only
if [ "$FULL" = 1 ] && [ -z "$ONLY" ]; then
  hr "test (full)"
  if [ -n "${FULL_TESTS:-}" ]; then
    if have pytest || have_mod pytest; then
      if "$PY" -m pytest $FULL_TESTS -q; then pass "full suite"; else fail "full suite"; fi
    else
      skip "pytest not available"
    fi
  else
    skip "no FULL_TESTS declared"
  fi
fi

# ---------------------------------------------------------------- verdict
hr "verdict"
if [ ${#SKIPPED[@]} -gt 0 ]; then
  printf '   skipped: %s\n' "${SKIPPED[*]}"
fi
if [ ${#FAILED[@]} -gt 0 ]; then
  printf '   \033[31mFAILED: %s\033[0m\n' "${FAILED[*]}"
  exit 1
fi
if [ ${#SKIPPED[@]} -gt 0 ] && [ -z "${FAST_TESTS:-}" ] && [ -z "${FAST_SCRIPTS:-}" ] \
   && [ "${ALLOW_NO_TESTS:-0}" != 1 ]; then
  printf '   \033[33mPASSED, but with nothing asserted — declare FAST_TESTS/FAST_SCRIPTS\033[0m\n'
  printf '   (or set ALLOW_NO_TESTS=1 for a docs-only repo)\n'
  exit 1
fi
printf '   \033[32mall gates passed\033[0m\n'
exit 0
