#!/usr/bin/env python3
"""
Parsnips — watch for a new sitting, and publish it.

WHAT THIS IS FOR
The archive is 334 sittings and grows. A sitting is announced in advance, the Hansard
appears within 7 working days of it, and nothing tells the site about either. This is the
thing that notices and runs the pipeline, unattended.

WHY IT READS THE ANNOUNCEMENT AND NOT JUST THE HANSARD
The obvious probe — ask the Hansard for a date and see if reports come back — is a lagging
signal, and the lag is both long and variable. Measured on 2026-09-25: the sittings of
8, 9 and 10 September were on sprs.parl.gov.sg and **completely absent from the cached
calendar**, because that calendar was probed on 2026-09-16, inside the publication window,
and found nothing. Probing tells you a sitting happened only after it is already late.

Parliament announces the next sitting on its own homepage ("Parliament will be sitting on
6 October 2026"), which is a leading signal, so the watcher polls that. It then waits for
the Hansard to actually appear before running anything, because fetching too early gets a
partial record that looks complete.

THE TWO PHASES
  1. ANNOUNCE   read the homepage. A new announced date is recorded as pending.
  2. HARVEST    for any pending date in the past, ask the Hansard whether it has landed.
                When it has, run the chain and publish.

State lives in tools/.watch-state.json so a re-run never redoes finished work, and so the
cheap case (announcement unchanged, nothing pending) exits in under a second without
touching the archive.

THE CHAIN, AND WHY IT IS NOT OPTIONAL
    backfill --dates        fetch the sitting
    build_dataset           sentence ids, tiers, chunks (no model)
    build_briefs            extract + assemble + verify (the ONE model stage)
    promote_briefs          move only gate-passing briefs into summaries/
    build_summaries         load into SQLite with the FK anchors
    export_read             render the 332 pages into site/dist
    (gate)                  the export must print EXPORT VERIFIED
    commit + push           Vercel deploys site/dist from the push

FAIL CLOSED. If the export gate does not verify, nothing is committed and the run reports
a failure. A half-summarised sitting does not get published because the step after it
happened to exit zero (R-2.8).

Usage
    python3 tools/watch_sittings.py --status        # what it knows, no network
    python3 tools/watch_sittings.py --dry-run       # check the announcement, act on nothing
    python3 tools/watch_sittings.py                 # the real thing
    python3 tools/watch_sittings.py --quiet         # print nothing unless there is news
"""

import argparse
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "tools", ".watch-state.json")
LOCK = os.path.join(ROOT, "tools", ".watch.lock")
MANIFEST = os.path.join(ROOT, "data", "manifest.json")
ANNOUNCE_URL = "https://www.parliament.gov.sg/"

# The announced sitting has to be in the past before the Hansard can exist. The portal says
# "within 7 working days from the adjournment", so that is the deadline the watcher reports
# against -- but it polls from day one, because Hansard often appears sooner and there is no
# way to know from outside.
PUBLISH_WORKING_DAYS = 7

MONTHS = ("January|February|March|April|May|June|July|August|September|October|"
          "November|December")
# The first date of a range often has no year ("from 24 February to 6 March 2026"), so the
# year is optional on it and inherited from the end date when missing. Requiring it on both
# silently matched nothing, which is the worst outcome here: a real announcement read as
# "no sitting".
RE_SITTING_RANGE = re.compile(
    rf"sitting from\s+(\d{{1,2}}\s+(?:{MONTHS}))(?:\s+(\d{{4}}))?\s+to\s+"
    rf"(\d{{1,2}}\s+(?:{MONTHS})\s+(\d{{4}}))", re.I)
RE_SITTING_ON = re.compile(rf"sitting (?:on|from)\s+(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})", re.I)
RE_ADJOURNED_TO = re.compile(rf"adjourned to\s+(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})", re.I)
RE_ADJOURNED_FIXED = re.compile(r"adjourned to a date to be fixed", re.I)


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------- announce


def fetch_announcement(timeout=40):
    """The homepage's ANNOUNCEMENT block, as plain text."""
    req = urllib.request.Request(ANNOUNCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r"ANNOUNCEMENT(.{0,900}?)(?:FIND MP|LATEST NEWS|</section>)", html, re.S | re.I)
    block = m.group(1) if m else html
    txt = re.sub(r"<[^>]+>", " ", block)
    txt = re.sub(r"&nbsp;?", " ", txt)
    return " ".join(txt.split())


def parse_announcement(text):
    """-> (announced dates, note). Never guesses: an unparseable announcement is reported as
    such rather than treated as 'no sitting', because those two mean opposite things."""
    dates, note = [], ""
    if not text:
        return dates, "could not read the announcement block"

    m = RE_SITTING_RANGE.search(text)
    if m:
        start_txt, start_year, end_txt = m.group(1), m.group(2), m.group(3)
        # "24 February" with no year inherits it from the end date. If that makes the start
        # later than the end, the range crossed a new year, so it belongs to the previous one.
        if start_year:
            a = to_date(f"{start_txt} {start_year}")
        else:
            end_year = int(m.group(4))
            a = to_date(f"{start_txt} {end_year}")
            b_probe = to_date(end_txt)
            if a and b_probe and a > b_probe:
                a = to_date(f"{start_txt} {end_year - 1}")
        b = to_date(end_txt)
        if a and b:
            d = a
            while d <= b:
                if d.weekday() < 5:          # sittings are weekdays; ranges exclude weekends
                    dates.append(d)
                d += datetime.timedelta(days=1)

    if not dates:
        for m in RE_SITTING_ON.finditer(text):
            d = to_date(m.group(1))
            if d:
                dates.append(d)

    if not dates:
        m = RE_ADJOURNED_TO.search(text)
        if m:
            d = to_date(m.group(1))
            if d:
                dates.append(d)

    if not dates:
        if RE_ADJOURNED_FIXED.search(text):
            note = "adjourned to a date to be fixed (no date named yet)"
        else:
            note = "announcement carried no parsable sitting date"

    # dedupe, keep order
    seen, out = set(), []
    for d in dates:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out, note


def to_date(s):
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


# ----------------------------------------------------------------- state


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {"sittings": {}, "runs": []}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def archived_dates():
    """The sittings that are actually ON THE SITE: those with a rendered page in site/dist.

    Deliberately NOT the sitting JSON, and not the manifest's keys. Both are earlier than the
    end of the chain, so either would report a sitting as done while the failure that matters
    was still ahead of it:

      * the JSON exists as soon as `backfill` finishes -- if `briefs` or the export then fails,
        the sitting has raw data and no page;
      * the manifest carries 23 out-of-scope 2015 entries with no payload behind them, so
        counting its keys reports 357 for an archive of 334.

    A rendered page is the last thing the chain produces, so it is the only honest signal that
    a sitting is published. Anything with data but no page is picked up as work to finish.
    """
    import glob
    out = set()
    for p in glob.glob(os.path.join(ROOT, "site", "dist", "20*-*-*.html")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.html$", p)
        if m:
            out.add(m.group(1))
    return out


def fetched_dates():
    """Sittings with raw data on disk. Used only to report a half-finished chain."""
    import glob
    out = set()
    for p in glob.glob(os.path.join(ROOT, "data", "20*", "sitting_*.json")):
        m = re.search(r"sitting_(\d{4}-\d{2}-\d{2})\.json$", p)
        if m:
            out.add(m.group(1))
    return out


def working_days_since(d):
    n, day = 0, d
    today = datetime.date.today()
    while day < today:
        day += datetime.timedelta(days=1)
        if day.weekday() < 5:
            n += 1
    return n


# ----------------------------------------------------------------- hansard probe


def hansard_has(date, timeout=90):
    """Is this sitting on sprs.parl.gov.sg yet? Asks the portal directly for that one date.

    A date with no reports is not necessarily unpublished -- the portal 7-day lag means an
    absence is expected early on -- so this is only ever read together with the working-day
    count, never on its own."""
    cmd = [sys.executable, os.path.join(ROOT, "scraper", "parsnips_fetch.py"),
           "--discover", date.isoformat(), date.isoformat()]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"(\d+)\s+sitting\(s\)", r.stdout or "")
    if not m:
        return None
    return int(m.group(1)) > 0


# ----------------------------------------------------------------- the chain


def run(cmd, label, timeout=3600):
    log(f"{label}: {' '.join(cmd[1:])}")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    ok = r.returncode == 0
    log(f"{label}: {'ok' if ok else 'FAILED'} ({time.time()-t0:.0f}s)")
    if not ok:
        tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-800:]
        log(tail)
    return ok, r.stdout or ""


def publish(dates, model, dry_run=False):
    """Run the whole chain for these dates. Returns (ok, notes)."""
    notes = []
    ds = [d.isoformat() for d in dates]
    scratch = "/tmp/briefs_watch"

    if dry_run:
        return True, [f"dry run: would process {', '.join(ds)}"]

    steps = [
        ("fetch", [sys.executable, "scraper/backfill.py", "--dates", *ds], 3600),
        ("dataset", [sys.executable, "summariser/build_dataset.py"], 3600),
        # The chunk plan and the report/turn/sitting tables. These two were missing from the
        # chain, and without them a new sitting is fetched and summarised but never reaches
        # the database the exporter renders from: the export silently produced the old page
        # count, and the summary load died on a foreign key to a report row that was never
        # inserted. Both rebuild from everything on disk, not just the new dates, which is
        # what keeps their output a function of the corpus rather than of this run.
        ("chunks", [sys.executable, "rag/build_chunks.py"], 3600),
        ("db", [sys.executable, "rag/build_db.py"], 3600),
    ]
    for label, cmd, tmo in steps:
        ok, _ = run(cmd, label, tmo)
        if not ok:
            return False, notes + [f"{label} failed"]

    env = dict(os.environ, PARSNIPS_OUT=scratch)
    log("briefs: extract + assemble + verify (the model stage)")
    t0 = time.time()
    r = subprocess.run([sys.executable, "summariser/build_briefs.py", "--dates", *ds,
                        "--model", model],
                       capture_output=True, text=True, timeout=14400, cwd=ROOT, env=env)
    log(f"briefs: {'ok' if r.returncode == 0 else 'FAILED'} ({time.time()-t0:.0f}s)")
    if r.returncode != 0:
        log(((r.stdout or "") + (r.stderr or ""))[-1500:])
        return False, notes + ["briefs failed"]
    m = re.search(r"(\d+)\s+(?:brief|item)", r.stdout or "")
    if m:
        notes.append(f"briefs produced: {m.group(1)}")

    for label, cmd, tmo in [
        ("promote", [sys.executable, "tools/promote_briefs.py", "--from", scratch], 1800),
        # --rebuild is not optional here. The tables always exist after the first run, and
        # build_summaries.py exits 2 rather than loading into them, so the plain command
        # fails on every run after the very first. Promotion already rewrote the briefs on
        # disk, so the tables have to be rebuilt from them or the new sittings are promoted
        # but never reach the site.
        ("sqlite", [sys.executable, "rag/build_summaries.py", "--rebuild"], 3600),
        ("export", [sys.executable, "rag/export_read.py", "--out", "site/dist"], 3600),
    ]:
        ok, out = run(cmd, label, tmo)
        if not ok:
            return False, notes + [f"{label} failed"]
        if label == "export":
            # FAIL CLOSED. The gate's verdict, not the exit code, decides publication.
            if "RESULT: EXPORT VERIFIED" not in out:
                log("export gate did not verify -- refusing to commit")
                return False, notes + ["export gate did not verify"]
            notes.append("export gate: VERIFIED")
    return True, notes


def git_publish(dates, notes):
    """Commit the new sitting and push. Vercel deploys site/dist from the push."""
    ds = ", ".join(d.isoformat() for d in dates)
    msg = (f"Publish the sitting of {ds}\n\n"
           "Fetched, summarised, verified and rendered by tools/watch_sittings.py.\n"
           + "\n".join(f"  {n}" for n in notes) + "\n")
    for cmd in (["git", "add", "-A"],
                ["git", "commit", "-q", "-m", msg],
                ["git", "push", "origin", "HEAD"]):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=600)
        if r.returncode != 0:
            log(f"git {cmd[1]}: FAILED\n{(r.stderr or '')[-600:]}")
            return False
    return True


# ----------------------------------------------------------------- main


def acquire_lock():
    """Take an exclusive lock so two runs cannot fight over the scratch dir and the archive.

    Two concurrent runs share /tmp/briefs_watch, and both promote out of it, so one can read
    a brief while the other is halfway through replacing it. That does not fail cleanly: the
    chain dies at the promote step, after the slow and expensive model stage has already run,
    which is the worst possible place to lose a run.

    flock is released by the kernel when the process exits, including on a crash or a kill, so
    a dead run cannot leave the watcher permanently locked out. Returns the open handle (keep
    it alive for the process lifetime) or None if another run holds the lock.
    """
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("PARSNIPS_LLM_MODEL",
                                                     "deepseek-v4.1-flash:cloud"))
    ap.add_argument("--dry-run", action="store_true",
                    help="read the announcement and report; change nothing")
    ap.add_argument("--status", action="store_true", help="show state; no network")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing unless there is news or a failure")
    ap.add_argument("--force-date", action="append", default=None,
                    help="treat this date as ready to harvest (repeatable)")
    args = ap.parse_args(argv)

    st = load_state()

    if args.status:
        print(json.dumps(st, indent=1, sort_keys=True))
        return 0

    # --dry-run and --status change nothing, so they do not need the lock and must not be
    # blocked by a run that holds it: the whole point of a dry run is inspecting while
    # something else is going on.
    lock = None
    if not args.dry_run:
        lock = acquire_lock()
        if lock is None:
            log("another run holds the lock; exiting without doing anything")
            return 0
    _ = lock  # held for the process lifetime; flock releases on exit

    today = datetime.date.today()
    archived = archived_dates()
    fetched = fetched_dates()
    # A sitting with raw data but no rendered page is a half-finished chain. site/dist is the
    # authority on what is published, not the state file, which would call it done because a
    # previous run's fetch succeeded. These are not just reported -- they are put back into the
    # work list, because the fix is to finish the chain, and a watcher that can only complain
    # about unfinished work cannot do its one job unattended.
    unfinished = sorted(fetched - archived)
    news, failures = [], []
    if unfinished:
        news.append("half-finished chain, will complete: " + ", ".join(unfinished))

    # ---------------------------------------------------------- phase 1
    try:
        text = fetch_announcement()
    except Exception as exc:
        failures.append(f"could not fetch the announcement: {type(exc).__name__}: {exc}")
        text = None

    if text is not None:
        dates, note = parse_announcement(text)
        st["last_announcement"] = text[:300]
        st["last_checked"] = today.isoformat()
        if note:
            st["last_note"] = note
        for d in dates:
            k = d.isoformat()
            rec = st["sittings"].setdefault(k, {})
            if not rec:
                rec["first_seen"] = today.isoformat()
                rec["status"] = "announced"
                news.append(f"new sitting announced: {k}")
            rec["announced"] = True

    # ---------------------------------------------------------- phase 2
    ready = []
    for k, rec in sorted(st["sittings"].items()):
        if rec.get("status") in ("published", "skipped"):
            continue
        if k in archived:
            rec["status"] = "published"
            rec["note"] = "already in the archive"
            continue
        d = datetime.date.fromisoformat(k)
        if d > today:
            continue                          # not happened yet
        if args.force_date and k not in args.force_date:
            continue
        if args.force_date:
            ready.append(d)
            continue
        got = hansard_has(d)
        elapsed = working_days_since(d)
        if got:
            ready.append(d)
            rec["status"] = "harvesting"
            news.append(f"{k}: Hansard has landed ({elapsed} working days after the sitting)")
        elif got is None:
            rec["status"] = "probe-failed"
            failures.append(f"{k}: could not probe the Hansard")
        else:
            rec["status"] = "waiting"
            rec["working_days_elapsed"] = elapsed
            if elapsed >= PUBLISH_WORKING_DAYS:
                failures.append(
                    f"{k}: {elapsed} working days after the sitting and still no Hansard "
                    f"(the portal promises {PUBLISH_WORKING_DAYS})")

    # Sittings whose chain stopped early are ready now, whether or not their date was ever
    # announced -- they are already fetched, so there is nothing to wait for.
    for k in unfinished:
        d = datetime.date.fromisoformat(k)
        if d not in ready:
            ready.append(d)
        st["sittings"].setdefault(k, {})["status"] = "harvesting"

    # ---------------------------------------------------------- act
    published = []
    if ready and not args.dry_run:
        ok, notes = publish(ready, args.model)
        if ok:
            if git_publish(ready, notes):
                for d in ready:
                    st["sittings"][d.isoformat()]["status"] = "published"
                published = [d.isoformat() for d in ready]
                news.append(f"published: {', '.join(published)}")
                for n in notes:
                    news.append(f"  {n}")
            else:
                failures.append("the chain succeeded but the commit or push failed")
        else:
            # Leave them in the work list rather than marking them anything final: the next run
            # picks them up from site/dist, which is the authority, and finishes the chain.
            for d in ready:
                st["sittings"].setdefault(d.isoformat(), {})["status"] = "incomplete"
            failures.append("the pipeline failed; nothing was published")
    elif ready and args.dry_run:
        news.append(f"dry run: {[d.isoformat() for d in ready]} are ready to harvest")

    st["runs"] = (st.get("runs") or [])[-19:] + [{
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "announced": [d.isoformat() for d in (parse_announcement(text)[0] if text else [])],
        "ready": [d.isoformat() for d in ready],
        "published": published,
        "failures": failures,
    }]
    if not args.dry_run:
        save_state(st)

    # ---------------------------------------------------------- report
    if args.quiet and not news and not failures:
        return 0
    if not news and not failures:
        pending = [k for k, r in st["sittings"].items()
                   if r.get("status") in ("announced", "waiting")]
        waiting = ", ".join(pending) if pending else "none"
        log(f"nothing to do. archive has {len(archived)} sittings; "
            f"awaiting the Hansard: {waiting}.")
        return 0
    for n in news:
        print(n)
    for f in failures:
        print(f"FAILED  {f}")
    # a failure is a non-zero exit so a scheduler can tell the two apart
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
