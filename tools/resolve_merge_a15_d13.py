#!/usr/bin/env python3
"""Resolve the a15 x D13 merge conflicts in site/build_site.py AND site/dist/theme.css.

theme.css is generated from build_site.py's STYLE block, so the two files carry the same three
conflicts in the same order; resolving them differently would make the committed stylesheet stop
matching its own generator. So the resolution is written once, here, and applied to both files.

WHY BOTH SIDES, NOT ONE. The conflict is in the bottom-chrome token layer and EITHER side alone is
silently wrong:
  * keep only D13's side  -> `--z-pbar`/`--z-totop` disappear while this branch's rules still
    reference them, so the bar and the button get `z-index:auto`;
  * keep only this branch's side -> `--chrome-h` is never set to the bar's height at desktop, so
    `.totop{bottom:calc(var(--chrome-h) + 16px)}` collapses to 16px and D13's 10px overlap returns
    at every desktop width.
D13's comment on --chrome-h says --pbar-h is stated once; on THIS branch it is already stated once
in the main :root (it arrived with the column work), so the resolution keeps that single statement
and adds only --chrome-h, rather than re-declaring the height in a second place.
"""
import re
import sys

TOTOP_HEAD = ".totop{position:fixed;right:16px;bottom:16px;z-index:var(--z-totop);width:44px;height:44px;"
TOTOP_BOTH = (".totop{position:fixed;right:16px;bottom:calc(var(--chrome-h) + 16px);\n"
              "  z-index:var(--z-totop);width:44px;height:44px;")

PBAR_HEAD = ("  .pbar{display:block;position:fixed;left:0;right:0;bottom:0;z-index:var(--z-pbar);\n"
             "    height:var(--pbar-h);background:var(--ink);color:#fff;font-size:11px;\n"
             "    line-height:var(--pbar-h);letter-spacing:.06em;text-transform:uppercase}")
PBAR_BOTH = ("  /* The bar is the only element pinned to the floor here. --pbar-h is stated ONCE, in\n"
             "     the main :root -- it arrived there with the column work -- so this block adds only\n"
             "     --chrome-h, the height the button clears, and the three fixed elements cannot drift\n"
             "     apart. (D13's own commit states --pbar-h here because on its base it did not exist\n"
             "     yet; on the merged tree that would be a second place to keep in step by hand.) */\n"
             "  :root{--chrome-h:var(--pbar-h)}\n"
             "  .pbar{display:block;position:fixed;left:0;right:0;bottom:0;z-index:var(--z-pbar);\n"
             "    height:var(--pbar-h);background:var(--ink);color:#fff;font-size:11px;\n"
             "    line-height:var(--pbar-h);letter-spacing:.06em;text-transform:uppercase}")

TOAST_BOTH = "  /* The toast sits above the bar, not on it.\n" \
             "     THIS IS THE ONLY PLACE `bottom` IS SET FOR .resume WHEN BOTH ARE LIVE, so it is also\n" \
             "     the only place the coupling between the two can be stated. It was the bare literal\n" \
             "     `38px` -- the bar's 26px plus 12px of air held only in the author's head -- and it is\n" \
             "     now that same arithmetic, so the toast clears whatever height the bar is given.\n" \
             "     (Audit D14 reported that the desktop rule \"never sets bottom\", making the desktop\n" \
             "     toast 16px and behind the bar. That is not what the sheet does: the base rule at the\n" \
             "     foot of the file sets 16px for the PHONE, where .pbar is display:none, and this block\n" \
             "     -- later in the sheet -- overrides it. Measured by constructing the real toast: bottom\n" \
             "     38px, toast/bar overlap 0px at 1024/1280/1440/1920, asserted by\n" \
             "     tools/check_outline.py's D14 check and by the D13 card's check_bottom_chrome.py. */"

RESOLUTIONS = [TOTOP_BOTH + "\n", PBAR_BOTH + "\n", TOAST_BOTH + "\n"]
# The label after `<<<<<<<`/`>>>>>>>` is the ref name git happened to use: `HEAD`/`wt/t_2a44f62f`
# on a fresh merge, `ours`/`theirs` after `git checkout --merge`. Match either.
PAT = re.compile(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", re.S)


def resolve(path):
    src = open(path).read()
    hunks = PAT.findall(src)
    if len(hunks) != len(RESOLUTIONS):
        raise SystemExit("%s: %d conflicts, expected %d"
                         % (path, len(hunks), len(RESOLUTIONS)))
    for i, (mine, theirs) in enumerate(hunks):
        # Sanity: the hunk we are resolving is the one we think it is.
        want = (TOTOP_HEAD, PBAR_HEAD)[i] if i < 2 else "  /* The toast sits above the bar"
        if not mine.startswith(want):
            raise SystemExit("%s hunk %d: HEAD side is not what this resolver expects:\n%s"
                             % (path, i, mine[:120]))
    out = src
    for repl in RESOLUTIONS:
        out = PAT.sub(repl.replace("\\", "\\\\"), out, count=1)
    if "<<<<<<<" in out or ">>>>>>>" in out:
        raise SystemExit("%s: markers remain" % path)
    open(path, "w").write(out)
    print("%s: resolved %d conflict(s)" % (path, len(hunks)))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        resolve(p)
