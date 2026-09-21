# `tools/compare_renders.py` — the hardcoded worktree path, and what fixing it exposed

Card `t_01c45b8d`, branch `parsnips/t_01c45b8d-tools-compare_renders.py-hardcodes-a-sib`,
based on `82d4bb3` (the root card's frozen artifact).

## 1. The named defect — fixed

    - ROOT = "/Users/shawnlin/parsnips/.worktrees/t_b764176e"
    + ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

`ROOT` is used three ways and all three now resolve to THIS tree: `ROOT/tools` (the import path
for `check_column_guides`), `ROOT/site/dist` (the after side it serves and screenshots), and the
`cwd` for `git show HEAD~1:site/dist/theme.css`.

Reproduced BEFORE the fix, with the `t_b764176e` worktree renamed aside
(`raw-prefix-compare_renders-crash.log`):

    FileNotFoundError: [Errno 2] No such file or directory:
        '/Users/shawnlin/parsnips/.worktrees/t_b764176e/site/dist'
    exit 1   <- a broken tool, not a stale path

## 2. `tools/qa_frames.py` does not import from it — the card's premise is wrong there

The card (echoing `docs/layout-qa/QA-REPORT.md` §5.2) says `qa_frames.py` imports `SETTLE` from
`compare_renders.py`, so "both tools break together". It does not, and it never did:

    tools/qa_frames.py:17  HERE = os.path.dirname(os.path.abspath(__file__))
    tools/qa_frames.py:18  sys.path.insert(0, HERE)
    tools/qa_frames.py:19  from check_column_guides import CHROME, CDP
    tools/qa_frames.py:20  from qa_pixels import SETTLE          <-- qa_pixels, not compare_renders

`git log -S compare_renders -- tools/qa_frames.py` is empty; `qa_frames.py` was added by `1bfea60`
and `compare_renders.py` by `487fdc7`, on branches that only met at the `8bc6300` merge, so the
import the report describes never existed in any commit. Asserted directly, with the stale worktree
absent: the version of `qa_frames.py` committed at `82d4bb3` exits **0**
(`raw-prefix-qa_frames-no-import.log`). `qa_frames.py` was already path-independent; it needed no
change and got none.

## 3. The defect the card did not name — the default before-side was HEAD-relative

Not every tree's `HEAD~1` is the commit that changed the stylesheet. On this card's tree — based on
the root card's integration commit — `HEAD~1:site/dist/theme.css` is identical to the working copy
(69 657 chars, `differ=False`), so the default would compare the CURRENT stylesheet with itself and
report every desktop width `UNEXPECTED` with 0 changed pixels. Measured, before that was fixed:
`raw-postfix-compare_renders-default-noguard.log`. That reads as a broken comparison, not as a
wrong tool invocation — the same failure class as the hardcoded path.

So the default now **refuses** instead of silently comparing a stylesheet with itself. The message
in the log as committed (`raw-postfix-compare_renders-default.log`) is this text with the tool's
`  ` indent applied; the same guard body disabled in a throwaway copy reproduces the silent
`UNEXPECTED` run it prevents (`raw-postfix-compare_renders-default-noguard.log`, 4 UNEXPECTED at
760/761/1024/1440, 0 px changed, exit 1):

    REFUSING: HEAD~1 carries the same theme.css as the working tree, so this tree's HEAD is not
    the commit that changed the stylesheet ... Name the pre-change build instead:
      python3 tools/compare_renders.py <after-dir|url> <before-dir|url>

and the tool takes an explicit after/before pair — a directory (served here) or an http base url
(used as-is), the same two forms `qa_pixels.py` and `qa_frames.py` already take — so it can be run
from any tree, against any pre-change build, at any time:

    python3 tools/compare_renders.py                                     # HEAD~1 default
    python3 tools/compare_renders.py site/dist <pristine>/site/dist
    python3 tools/compare_renders.py http://127.0.0.1:8477 http://127.0.0.1:8478

## 4. Two things found while making it run, both fixed

1. **The capture was not reliable at phone/tab widths.** `shot()` set the device metrics and waited
   0.8 s before settling. On the first measurement at 390px the frame came back at full desktop
   height (99070 vs 211323 state mismatch, and 390px reported `UNEXPECTED` with 103 567 changed
   pixels — the two sides were the same frame captured at two different layouts). The reflow wait is
   now 1.5 s, and a probe capturing 1440/390/1440/1440 in sequence returns each frame at its own
   width (`probe`: `asked 1440 frame (1440,900)` … `asked 390 frame (390,900)` …). Without this the
   tool's phone/tablet claim was not trustworthy at the widths it is cited for.
2. **The x-column count printed but did not assert.** The first version failed the run whenever the
   changed pixels spanned more than `width/8` columns; the real frames span 209-225 columns, because
   a 1px hairline over a 211 323px-tall document legitimately touches hundreds of rows. The x
   histogram is evidence (it shows the changes land on a handful of 1px columns, not across the
   layout); the hard failure is now a frame that is mostly different (>20% of pixels), which would
   mean the two sides are not the same page. The column count is printed for every width.

## 5. Acceptance: both tools, with the stale worktree unavailable

`t_b764176e` renamed to `t_b764176e.moved-aside` for the whole of this section, restored afterwards
(`git worktree list` and its worktree both verified intact afterwards).

**`compare_renders.py`, explicit form** — `python3 tools/compare_renders.py site/dist
<baseline-140a0b87>/site/dist`, exit **0** (`raw-postfix-compare_renders-explicit.log`):

    self-check (same url, two sessions): 0 px differ
     width  verdict                        changed px / total   guide columns (x)
       390  IDENTICAL (as required)        0 / 351000   none
       759  IDENTICAL (as required)        0 / 683100   none
       760  DIFFERS (as required)          1090 / 684000   4 x, 30..683
       761  DIFFERS (as required)          9192 / 684900   225 x, 24..760
      1024  DIFFERS (as required)          9205 / 921600   225 x, 24..1023
      1440  DIFFERS (as required)          14505 / 1296000   209 x, 214..1423
    PASS

The before side is the pristine pre-change build (`994a1df` + a fresh generator run), a different
page from the merged build in markup and payloads as well as stylesheet — which is what
`tools/qa_pixels.py` already does, and its recorded run on this same merged build agrees at every
width it covers (`docs/layout-root/raw-qa_pixels.log`: 0 / 0 / 866 / 8940 / 9205 / 14505).

**`compare_renders.py`, default form** — `python3 tools/compare_renders.py`, exit **1** and a
message, not a traceback (`raw-postfix-compare_renders-default.log`), and with a stylesheet that
really does differ from `HEAD~1` it runs the full comparison and reports the desktop widths honestly
(`raw-postfix-compare_renders-roothform.log`, 4 UNEXPECTED — correct: an appended no-op CSS comment
is not a change that draws anything). The dist was reverted immediately; `git status site/dist` is
empty.

**`qa_frames.py`** — `python3 tools/qa_frames.py http://127.0.0.1:8490 http://127.0.0.1:8491
<out-dir>`, exit **0**, 16 frames written, run twice — once with `t_b764176e` renamed aside
(`raw-postfix-qa_frames-stale-aside.log`) and once with it present as a control
(`raw-postfix-qa_frames-stale-present.log`). The frames are byte-identical to the set already
committed under `docs/layout-qa/before-after/`, all 16 of them (`shasum -a 256`, recorded in §7).

Both tools' runs above were made with `t_b764176e` unavailable, which is the acceptance condition
the card asks for; the worktree was restored after each one and `git worktree list` shows it intact.

**Re-run on the committed tree**, after `7f988cb9`, with `t_b764176e` renamed aside again:
`compare_renders.py` exit 0 with the same six verdicts (0 / 0 / 1090 / 9192 / 9205 / 14505), and
`qa_frames.py` exit 0. As a control, the same `qa_frames.py` invocation with the stale worktree
present writes byte-identical frames (75 407 / 102 112 / 103 417 / 103 164 / 111 999 / 129 028 /
137 033 / 154 162 bytes) — the tool's output does not depend on that worktree either way.

## 6. What this does not settle

1. **`tools/` is not on `main`.** Everything here — including the tool this card fixes — exists only
   on `parsnips/t_a1329966-ui-improvements-for-columns-on-desktop` (and branches descending from it,
   this one included). `git cat-file -e main:tools/compare_renders.py` fails. `docs/layout-qa/
   QA-REPORT.md` §6 nonetheless tells the reader to run `python3 tools/check_rail_preview.py` and
   friends "from a pristine tree", and the report's §5.2 finding was written as though
   `compare_renders.py` were a repo tool. It is a branch artifact that has to be merged before any
   of it is reachable; whether it should live in `tools/` at all, or beside the evidence it makes
   under `docs/`, is a decision this card did not make. Follow-up card opened.
2. **The two sides are not one-variable** in the explicit form, and the tool now says so on stdout
   (`after =`, `before =`). The HEAD~1 form remains the only one that varies the stylesheet alone;
   that is why it is the default and why it refuses when it cannot be that.
3. **Frame byte-identity across sessions is still not an instrument** (the root card's caveat). The
   same-session self-check is now green on this tree (0 px), but the numbers above are a
   same-session comparison and should be read as such.
4. **`t_b764176e`'s worktree carries 27 uncommitted files** (`site/dist/pipeline/state.json` and 25
   `sittings/2017-*.html`) — the same staleness as card `t_79a0e9e2`. It was restored exactly as it
   was found; nothing in this card touched it.

## 7. Files and raw evidence

    M tools/compare_renders.py           the fix (ROOT, explicit before side, the two corrections)
    + docs/layout-tools-path/            this report + the raw logs + the frames it re-captured

    VERIFICATION.md                                   this report
    frames/                                           the 16 re-captured breakpoint frames
    raw-prefix-compare_renders-crash.log              BEFORE the fix, stale worktree aside: FileNotFoundError
    raw-prefix-qa_frames-no-import.log                qa_frames.py as committed at 82d4bb3: its imports, exit 0
    raw-postfix-compare_renders-default.log           after: default form refuses (exit 1), stale worktree aside
    raw-postfix-compare_renders-explicit.log          after: explicit form PASS (exit 0), stale worktree aside
    raw-postfix-compare_renders-roothform.log         after: default form with a stylesheet that differs
    raw-postfix-compare_renders-default-noguard.log   the guarded failure it prevents (guard disabled)
    raw-postfix-qa_frames-stale-aside.log             after: qa_frames exit 0, stale worktree aside
    raw-postfix-qa_frames-stale-present.log           after: qa_frames exit 0, control run
    raw-postfix-qa_frames-evidence-frames.log         the same run writing frames/ (byte-identical set)

Frame hashes — every one of the 16 files in `frames/` matches its counterpart in
`docs/layout-qa/before-after/` byte for byte (`shasum -a 256`, 16 pairs):

    after-390.png   ec49bbb4…  before-390.png   ec49bbb4…   (identical frames, as required)
    after-759.png   1ca85714…  before-759.png   1ca85714…
    after-760.png   c79f2263…  before-760.png   3c0275d7…   (differ: the guides)
    after-761.png   a83dbcb3…  before-761.png   08a767fa…
    after-390-scrolled.png  7d719e4b…  before-390-scrolled.png  7d719e4b…
    after-759-scrolled.png  ee94ac77…  before-759-scrolled.png  ee94ac77…
    after-760-scrolled.png  b1237ea7…  before-760-scrolled.png  5fa5cac2…
    after-761-scrolled.png  57e023da…  before-761-scrolled.png  19585c9d…
