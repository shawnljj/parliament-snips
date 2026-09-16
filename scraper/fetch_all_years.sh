#!/bin/bash
# Fetch 2017-2025, one year at a time, modern-first.
#
# Why a wrapper and not `--years 2017 2018 ... 2025`:
#   - Each year is a separate `backfill.py` invocation, so an interruption costs
#     only the current year's in-flight sittings. The per-year calendar cache
#     (data/calendar/<year>.json) means a resumed year does not re-probe.
#   - Progress is appended to backfill_history.log so a multi-hour unattended run
#     leaves a durable trail even if this session ends.
#   - A year that returns zero sittings is logged loudly and the run CONTINUES,
#     so one odd year cannot silently block the other eight.
#
# Pacing: the existing PAUSE/backoff in parsnips_fetch is left alone. The live API
# slows and returns HTTP 500 under sustained probing, so this is deliberately
# sequential across years and modest within them (ENUM_WORKERS=4, WORKERS=5).

set -u
cd /Users/shawnlin/parsnips || exit 1
HIST=backfill_history.log

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$HIST"; }

log "=== batch run start: 2017-2025 ==="

for YEAR in 2017 2018 2019 2020 2021 2022 2023 2024 2025; do
    log "--- $YEAR start ---"
    BEFORE=$(python3 -c "import json;m=json.load(open('data/manifest.json'));print(len(m['sittings']))")

    if python3 -u scraper/backfill.py --year "$YEAR" --discover 2>&1 | tee -a "$HIST"; then
        STATUS=ok
    else
        STATUS="exit $?"
    fi

    AFTER=$(python3 -c "import json;m=json.load(open('data/manifest.json'));print(len(m['sittings']))")
    ADDED=$((AFTER - BEFORE))

    if [ "$ADDED" -eq 0 ]; then
        log "!!! $YEAR added 0 sittings ($STATUS) -- INVESTIGATE, do not assume empty"
    else
        log "--- $YEAR done: +$ADDED sittings (total $AFTER) [$STATUS] ---"
    fi
done

log "=== batch run complete ==="
python3 scraper/backfill.py --status 2>&1 | tee -a "$HIST"
