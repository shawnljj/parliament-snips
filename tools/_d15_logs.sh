#!/bin/sh
# Write the D15 verification logs and the commit. Run from the task worktree.
set -e
cd /Users/shawnlin/parsnips/.worktrees/t_2bd5daf9

AFTER=http://127.0.0.1:8482
PRISTINE=http://127.0.0.1:8483
REF=http://127.0.0.1:8484
OUT=docs/layout-qa/d15
mkdir -p "$OUT"

echo "== qa_build_diff (after vs fresh build of the same commit) =="
python3 tools/qa_build_diff.py ../t_2bd5daf9-ref/site/dist > "$OUT/raw-qa_build_diff.log" 2>&1 || true

echo "== qa_d15_pixels =="
python3 tools/qa_d15_pixels.py "$AFTER" "$REF" "$OUT" > "$OUT/raw-qa_d15_pixels.log" 2>&1 || true

echo "== qa_archive_pbar =="
python3 tools/qa_archive_pbar.py > "$OUT/raw-qa_archive_pbar.log" 2>&1 || true

echo "== qa_scroll_geometry (settled) =="
python3 tools/qa_scroll_geometry.py "$AFTER" /sittings/index.html > "$OUT/raw-qa_scroll_geometry.log" 2>&1 || true

echo "== check_pbar (after) =="
python3 tools/check_pbar.py "$AFTER" > "$OUT/raw-check_pbar-after.log" 2>&1 || true

echo "== check_pbar (pristine pre-change: must FAIL) =="
python3 tools/check_pbar.py "$PRISTINE" > "$OUT/raw-check_pbar-pristine.log" 2>&1 || true

echo "== qa_defects D15 (after, 1280+1440) =="
python3 tools/qa_defects.py "$AFTER" 1280,1440 > "$OUT/raw-qa_defects-after.log" 2>&1 || true

echo "== qa_defects D15 (pristine pre-change: must FAIL) =="
python3 tools/qa_defects.py "$PRISTINE" 1280,1440 > "$OUT/raw-qa_defects-pristine.log" 2>&1 || true

echo "== qa_console =="
python3 tools/qa_console.py "$AFTER" > "$OUT/raw-qa_console.log" 2>&1 || true

echo "== check_rail =="
python3 tools/check_rail.py "$AFTER/sittings/2026-08-04.html" > "$OUT/raw-check_rail.log" 2>&1 || true

echo "== check_column_guides =="
python3 tools/check_column_guides.py "$AFTER/sittings/2026-08-04.html" > "$OUT/raw-check_column_guides.log" 2>&1 || true

echo "logs written"
ls "$OUT"
