#!/bin/sh
# Amend the D15 commit so the SUBJECT is the change, not the file's heading.
set -e
cd /Users/shawnlin/parsnips/.worktrees/t_2bd5daf9
tail -n +3 docs/layout-qa/d15/COMMIT-MESSAGE.txt > /tmp/d15-msg.txt
head -3 /tmp/d15-msg.txt
git commit --amend -F /tmp/d15-msg.txt
echo "== amended =="
git log --oneline -2
echo "== subject =="
git log -1 --format=%s
echo "== body first lines =="
git log -1 --format=%b | head -4
