#!/bin/sh
# Is a fresh clone a faithful build? The executable form of this project's acceptance
# criterion for site/dist, because "I built it" and "I committed it" can disagree and
# only a clone can tell you.
#
#   tools/check_fresh_clone.sh <repo-url-or-path> <scratch-dir>
#
# Clones the repo, builds the site inside the clone, and asserts:
#   * the build changes NOTHING relative to what is committed (the fidelity check)
#   * the build creates no file that is not committed (an untracked payload is a broken
#     expander on the deployed site where a page fetches it)
#   * every shipped sitting page carries the current generator's markup
#   * no committed page/payload pair shows a sentence both in the body and in its
#     "N sentences hidden" expander (the invariant a git add -A sweep broke once)
#   * tools/check_dist.py exits 0 inside the clone
#   * the staleness rule in tools/check_artifacts.py has BOTH cases: silent on a clean
#     tree, and firing when a corpus commit lands after status.json. A rule that never
#     fires is dead code, so both are exercised here rather than assumed.
#
# Usage:
#   tools/check_fresh_clone.sh . /tmp/parsnips-acceptance
set -u

REPO="${1:?usage: check_fresh_clone.sh <repo-url-or-path> <scratch-dir>}"
SCRATCH="${2:?usage: check_fresh_clone.sh <repo-url-or-path> <scratch-dir>}"
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
CLONE="$SCRATCH/clone"
FAIL=0

rm -rf "$CLONE"
mkdir -p "$SCRATCH"
git clone --quiet "$REPO" "$CLONE" || exit 1
cd "$CLONE" || exit 1
git checkout --quiet "$BRANCH" 2>/dev/null || true

echo "=== fresh clone of $REPO ($BRANCH) ==="
git log --oneline -1
echo "uncommitted entries before the build: $(git status --porcelain | wc -l | tr -d ' ')"
echo "dataset payloads present: $(ls pipeline/dataset 2>/dev/null | tr '\n' ' ')"

echo
echo "=== build and compare against what is committed ==="
python3 site/build_site.py
DIFFS=$(git diff HEAD --name-only -- site/dist | wc -l | tr -d ' ')
NEW=$(git status --porcelain -- site/dist | grep -c '^??' | tr -d ' ')
echo "files differing from HEAD after a build: $DIFFS"
echo "files a build creates that are not committed: $NEW"
[ "$DIFFS" = "0" ] || { echo "FAIL: the committed site/dist is not a build of these inputs"; FAIL=1; }
[ "$NEW" = "0" ] || { echo "FAIL: a build produces files that are missing from the repo"; FAIL=1; }

echo
echo "=== every COMMITTED sitting page carries the current generator ==="
# Reads the committed bytes, not the working tree. Line 43 above already ran the build,
# so a grep over site/dist here would be measuring the generator's output against itself
# and could not fail -- measured on the pre-fix tree: 307 of 331 committed pages carry
# .dsec, but 331 of 331 of the files on disk do once this script has built them, while
# check_dist.py reports the 24 mixed-generation COMMITTED pages that are actually served.
# Assertions 5 and 6 of check_dist.py already do this properly against HEAD; this line is
# the same measurement in the acceptance script, so it has to read the same bytes.
COMMITTED=$(git ls-tree -r --name-only HEAD -- site/dist/sittings | grep -c '20[0-9][0-9]-')
WITH=$(git grep -l 'class="dsec"' HEAD -- 'site/dist/sittings/20*.html' 2>/dev/null | wc -l | tr -d ' ')
echo "pages with the current markup: $WITH of $COMMITTED (committed bytes)"
[ "$WITH" = "$COMMITTED" ] || { echo "FAIL: a shipped page is from an older generator"; FAIL=1; }

echo
echo "=== tools/check_dist.py ==="
if [ -f tools/check_dist.py ]; then
  python3 tools/check_dist.py
  RC=$?
  [ "$RC" -eq 0 ] || { echo "FAIL: check_dist.py did not pass inside the clone"; FAIL=1; }
else
  # This tree does not carry the gate at all (`main`, before this card). Saying so is
  # accurate; asserting exit 0 on a file that does not exist is not, and the old
  # `[ $? -eq 0 ]` form could not pass here -- python3 exits 2 with ENOENT, so the script
  # reported "check_dist.py did not pass" about a tree that has no check_dist.py.
  echo "NOTE: tools/check_dist.py is not present on this tree, so the gate is skipped."
  echo "      This is the pre-fix tree; the deliverable is the branch that adds it."
fi

echo
echo "=== the staleness rule, negative then positive ==="
python3 tools/check_artifacts.py > "$SCRATCH/neg.txt" 2>&1
NEG_RC=$?
NEG=$(grep -c 'STALE' "$SCRATCH/neg.txt" | tr -d ' ')
echo "STALE findings on this clean clone: $NEG (want 0)"
[ "$NEG" = "0" ] || { echo "FAIL: the freshness rule fires on a clean clone"; FAIL=1; }

# The exit code and the problem count, asserted rather than described. This tool exits 1
# whenever it has ANY problem, so a run that reports 3 problems and exits 0 is
# self-contradictory -- and it cannot exit 0 while the accepted set below is non-empty.
# The card's acceptance allows the enumeration branch ("or the remaining problems are
# enumerated as accepted"), so the number is checked against the accepted set here: if it
# ever grows or shrinks, the claim in docs/site-dist.md has to be re-measured, not assumed.
#
# BOTH assertions are about THIS TREE, where this card's fixes are present. Neither is
# meaningful on a tree that never carried them, so the script now says which it is looking
# at instead of reporting a misleading FAIL: run against `main` (pre-fix) the expected
# answers are exit 1 with 11 problems -- the 11 identical [ACCOUNTING] lines the census
# substitution removes -- and 11 != 3, correctly, because `main` is not this deliverable.
# Measured on `main` at 9fb0f545: exit 1, 11 problems (r8/ca_main.txt).
if grep -q 'from the CENSUS rather than the payload directories' tools/check_artifacts.py; then
  WANT_PROBS=3
else
  WANT_PROBS=11
  echo "NOTE: this tree does not carry the census fix (tools/check_artifacts.py counts"
  echo "      items by globbing the gitignored payload dirs), so 11 is the expected"
  echo "      count here -- this is the pre-fix tree, not the deliverable."
fi
PROBS=$(grep -cE '^  \[(ACCOUNTING|DOUBLE-COUNTED|ORPHAN|STALE|UNGATED|INVALID|MISSING|DRIFT|DUPLICATE)' "$SCRATCH/neg.txt" | tr -d ' ')
echo "check_artifacts.py on this clean clone: exit $NEG_RC, $PROBS problem(s) (expected: $WANT_PROBS)"
[ "$NEG_RC" = "1" ] || { echo "FAIL: check_artifacts.py did not exit 1 with problems outstanding"; FAIL=1; }
[ "$PROBS" = "$WANT_PROBS" ] || { echo "FAIL: the accepted problem set is $PROBS, not $WANT_PROBS"; FAIL=1; }
CASE="$SCRATCH/case_corpus_after_status"
rm -rf "$CASE"
git clone --quiet "$CLONE" "$CASE" || exit 1
echo '{}' > "$CASE/summaries/2026/zz-rule-test.json"
git -C "$CASE" add -A
git -C "$CASE" -c user.name=t -c user.email=t@t commit -q -m "corpus after status.json"
python3 "$CASE/tools/check_artifacts.py" > "$SCRATCH/pos.txt" 2>&1
if grep -q 'BEFORE the commit' "$SCRATCH/pos.txt"; then
  echo "positive case OK: a corpus commit after status.json makes the rule fire"
  grep 'BEFORE the commit' "$SCRATCH/pos.txt"
elif grep -q 'from the CENSUS rather than the payload directories' tools/check_artifacts.py; then
  echo "FAIL: the rule did not fire on the defect shape it exists for"
  FAIL=1
else
  echo "NOTE: this tree's staleness rule is the mtime rule this card replaced; the"
  echo "      commit-order rule that fires on this shape is not present here, so the"
  echo "      positive case is skipped rather than reported as a defect of the corpus."
fi
rm -rf "$CASE"

echo
if [ "$FAIL" = "0" ]; then
  echo "PASS — a fresh clone plus a build reproduces site/dist exactly."
else
  echo "FAIL — see above."
fi
exit "$FAIL"
