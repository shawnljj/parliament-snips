#!/bin/sh
# Run the D15 regression sweeps in sequence and keep every exit code.
cd /Users/shawnlin/parsnips/.worktrees/t_2bd5daf9 || exit 1
AFTER=http://127.0.0.1:8482
PRISTINE=http://127.0.0.1:8483

echo "### check_rail"
python3 tools/check_rail.py "$AFTER/sittings/2026-08-04.html"
echo "exit=$?"
echo

echo "### check_column_guides"
python3 tools/check_column_guides.py "$AFTER/sittings/2026-08-04.html"
echo "exit=$?"
echo

echo "### qa_pixels (after build vs pristine build)"
python3 tools/qa_pixels.py "$AFTER" "$PRISTINE"
echo "exit=$?"
echo

echo "### qa_archive_pbar"
python3 tools/qa_archive_pbar.py
echo "exit=$?"
echo

echo DONE
