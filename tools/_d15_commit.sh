#!/bin/sh
# Stage and commit the D15 change (source + tools + docs), deliberately excluding site/dist.
set -e
cd /Users/shawnlin/parsnips/.worktrees/t_2bd5daf9

git add site/build_site.py tools/qa_defects.py \
        tools/qa_build_diff.py tools/qa_archive_pbar.py tools/check_pbar.py \
        tools/qa_d15_pixels.py tools/qa_d15_shots.py tools/qa_scroll_geometry.py \
        tools/_d15_logs.sh docs/layout-qa/d15

echo "== staged =="
git diff --cached --name-only

git commit -F docs/layout-qa/d15/COMMIT-MESSAGE.txt
echo "== committed =="
git log --oneline -2
echo "== dist left untouched in the worktree? =="
git status --short -- site/dist | wc -l
