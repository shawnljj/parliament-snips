"""Prove the phone/tablet rendering is byte-for-byte unchanged, by diffing two real builds.

The acceptance criterion is "no change to mobile/tablet rendering". That is strongest tested by
building the PREVIOUS stylesheet and this one, rendering both, and comparing pixels:

  * at 390 / 759 the frames must be IDENTICAL (the guide block is inside @media
    (min-width:760px), and the only other edits are value-preserving token swaps),
  * at 760 / 761 / 1024 / 1440 they must DIFFER (the guides are there).

A difference at a phone width would be a regression; no difference at a desktop width would
mean the guides are not rendering.

DETERMINISM. Two sequential headless captures of the SAME page are not automatically identical:
the section rail shows a transient pill/bubble for ~700ms after any scroll, and a capture that
lands inside that window differs from one that does not. So before every capture this pins the
transient rail overlays hidden and switches off transitions, which isolates the comparison to
the stylesheet change. A self-check then captures the SAME url in two sessions and asserts zero
difference -- if that ever fails, this comparison is not measuring what it claims to.

USAGE. The after side is the tree this file lives in (ROOT/site/dist -- no other worktree is
named anywhere, so this works from a pruned-away sibling as readily as from the one it was
written in). The before side is either

  (a) HEAD~1's theme.css, copied over a copy of the after dist: the default, and the only form
      that varies exactly one thing (markup, JS and payloads stay byte-identical on both sides).
      It is the PRE-CHANGE stylesheet only if this tree's HEAD is the commit that changed
      theme.css, so the default refuses to run when HEAD~1 carries the same stylesheet; or

  (b) an explicit pre-change build -- a directory or an http base url -- served as a real build
      on both sides, the way tools/qa_pixels.py and tools/qa_frames.py already work.

    python3 tools/compare_renders.py                                     # (a)
    python3 tools/compare_renders.py site/dist <pristine>/site/dist      # (b)
    python3 tools/compare_renders.py http://127.0.0.1:8477 http://127.0.0.1:8478
"""
import base64
import io
import os
import subprocess
import sys
import tempfile
import time

# This tool serves and screenshots the tree it LIVES in: ROOT/site/dist is the after side and
# ROOT is the repo `git show HEAD~1:site/dist/theme.css` is read from. Deriving it from __file__
# (the idiom every other tool in tools/ uses) is what keeps it working when some other
# worktree -- the one it used to name -- is pruned, which is the normal end state of a card.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_column_guides as C                                    # noqa: E402
from PIL import Image, ImageChops                                  # noqa: E402

# Pin the non-deterministic chrome: the rail's pill and bubble are shown only while the reader
# is moving, and the scroll-memory toast can appear on a second visit.
SETTLE = """
(() => {
  const s = document.createElement('style');
  s.textContent = '*{transition:none !important;animation:none !important}'
    + '.section-rail-pill,.section-rail-bubble,.section-rail-go{opacity:0 !important}'
    + '.resume{display:none !important}';
  document.head.appendChild(s);
  window.scrollTo(0, 0);
  return 1;
})()
"""


def prepare_old_tree(dest):
    """A copy of the real dist with ONLY the stylesheet reverted to the parent commit.

    The experiment should vary one thing: the stylesheet. Building the parent's
    build_site.py into a bare directory also changes what the page can FETCH (the skipped
    payloads under dist/pipeline/), which makes the two pages expand to different heights and
    turns a stylesheet test into a content test. Copying dist and reverting theme.css keeps
    the markup, the JS and the payloads byte-identical on both sides.
    """
    import shutil
    src = os.path.join(ROOT, "site", "dist")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    old_css = subprocess.run(["git", "show", "HEAD~1:site/dist/theme.css"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    with open(os.path.join(dest, "theme.css"), "w", encoding="utf-8") as fh:
        fh.write(old_css)
    new_css = open(os.path.join(src, "theme.css"), encoding="utf-8").read()
    print(f"  old theme.css {len(old_css)} chars, new {len(new_css)} chars, "
          f"differ={old_css != new_css}")
    if old_css == new_css:
        sys.exit("REFUSING: HEAD~1 carries the same theme.css as the working tree, so this "
                 "tree's HEAD is not the commit that changed the stylesheet and the default "
                 "would compare identical stylesheets and report the desktop widths as "
                 "UNEXPECTED. Name the pre-change build instead:\n"
                 "  python3 tools/compare_renders.py <after-dir|url> <before-dir|url>")
    # The HTML must be identical on both sides for this to be a stylesheet test.
    same_html = 0
    for rel in ("sittings/2026-08-04.html", "sittings/index.html", "index.html"):
        a = open(os.path.join(src, rel), encoding="utf-8").read()
        b = open(os.path.join(dest, rel), encoding="utf-8").read()
        same_html += (a == b)
    print(f"  markup identical on both sides for {same_html}/3 sampled pages")


def shot(url, w, port, h=900):
    c = C.CDP(C.CHROME, url, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=False)
        time.sleep(1.5)                 # reflow at the new width before anything is pinned
        c.eval(SETTLE)
        time.sleep(1.2)                 # let the pinned state apply and any rAF settle
        data = c.call("Page.captureScreenshot", format="png")["result"]["data"]
        state = c.eval("(() => JSON.stringify({y: Math.round(window.scrollY),"
                       " h: document.documentElement.scrollHeight,"
                       " pw: document.documentElement.clientWidth}))()")
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB"), state
    finally:
        c.close()


def diff_stats(a, b):
    """changed pixels, and the x columns they sit in (the guides are 1px columns).

    The x histogram is what distinguishes "the stylesheet changed somewhere" from "a guide
    column moved", which is the whole claim at stake above 760px.
    """
    if a.size != b.size:
        return None, set()
    d = ImageChops.difference(a, b).tobytes()          # RGB: 3 bytes per pixel
    w = a.size[0]
    xs = set()
    changed = 0
    for i in range(0, len(d), 3):
        if d[i] or d[i + 1] or d[i + 2]:
            changed += 1
            xs.add((i // 3) % w)
    return changed, xs


def changed_px(a, b):
    return diff_stats(a, b)[0]


def serve(port, directory):
    return subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                             "--bind", "127.0.0.1", "--directory", directory],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def as_base(arg):
    """A build named as a directory (served here) or as an http base url (used as-is).

    Returns (base_url, None) for a url and (None, absolute_dir) for a directory.
    """
    if arg.startswith("http"):
        return arg.rstrip("/"), None
    return None, os.path.abspath(arg)


def main():
    after_arg = sys.argv[1] if len(sys.argv) > 1 else None
    before_arg = sys.argv[2] if len(sys.argv) > 2 else None

    servers = []
    if after_arg is None and before_arg is None:
        # (a) HEAD~1's stylesheet over a copy of THIS tree's dist.
        orig_dir = tempfile.mkdtemp(prefix="parsnips-original-", dir="/tmp")
        print("preparing the previous-STYLESHEET tree in", orig_dir)
        prepare_old_tree(orig_dir)
        new_base, old_base = f"http://127.0.0.1:8477", f"http://127.0.0.1:8478"
        servers.append(serve(8477, os.path.join(ROOT, "site", "dist")))
        servers.append(serve(8478, orig_dir))
    else:
        if after_arg is None or before_arg is None:
            sys.exit("give BOTH sides (after and before), or neither for the HEAD~1 default")
        after_side = as_base(after_arg)
        before_side = as_base(before_arg)
        new_base = after_side[0] or "http://127.0.0.1:8477"
        old_base = before_side[0] or "http://127.0.0.1:8478"
        for port, side in ((8477, after_side), (8478, before_side)):
            if side[1] is not None:
                servers.append(serve(port, side[1]))
        print(f"after  = {new_base}\nbefore = {old_base}")
    time.sleep(2.5)

    page = "sittings/2026-08-04.html"
    new_url = f"{new_base}/{page}"
    old_url = f"{old_base}/{page}"
    print(f"new = {new_url}\nold = {old_url}")
    if not new_url.startswith("http") or not old_url.startswith("http"):
        sys.exit("both url arguments must be http base urls")
    fails = []
    try:
        # ---- self-check: is this method even repeatable? -------------------------------
        a1, s1 = shot(new_url, 1440, 9981)
        a2, s2 = shot(new_url, 1440, 9982)
        repeat = changed_px(a1, a2)
        print(f"\nself-check (same url, two sessions): {repeat} px differ  "
              f"state {s1} vs {s2}")
        if repeat != 0:
            fails.append(f"self-check failed: two captures of the SAME page differ by "
                         f"{repeat} px, so this comparison cannot isolate the stylesheet")

        print(f"\n{'width':>6}  {'verdict':<30} changed px / total   guide columns (x)")
        for w, must_be_same in ((390, True), (759, True), (760, False),
                                (761, False), (1024, False), (1440, False)):
            a, sa = shot(old_url, w, 9983)
            b, sb = shot(new_url, w, 9984)
            if sa != sb:
                fails.append(f"{w}px: page state differs ({sa} vs {sb}) -- "
                             f"the comparison is not like-for-like")
            changed, xs = diff_stats(a, b)
            if changed is None:
                fails.append(f"{w}px: frame sizes differ {a.size} vs {b.size}")
                continue
            total = a.size[0] * a.size[1]
            same = changed == 0
            ok = (same == must_be_same)
            verdict = ("IDENTICAL (as required)" if must_be_same and same else
                       "DIFFERS (as required)" if (not must_be_same) and not same else
                       "UNEXPECTED")
            if not ok:
                fails.append(f"{w}px: {verdict} -- changed {changed} px, expected "
                             f"{'identical' if must_be_same else 'different'}")
            # The x histogram is evidence, not a verdict: it shows the desktop changes land on a
            # handful of 1px columns (the guides) rather than across the layout. Only a frame
            # that is mostly different is a hard failure -- that would mean the two sides are not
            # the same page at all, which would make the same/differ verdicts meaningless.
            cols = (f"{len(xs)} x, {min(xs)}..{max(xs)}" if xs else "none")
            if changed > total * 0.2:
                fails.append(f"{w}px: {changed}/{total} px differ ({100 * changed / total:.0f}%)"
                             f" -- the two sides are not the same page; this is not a "
                             f"stylesheet-level comparison")
            print(f"{w:>6}  {verdict:<30} {changed} / {total}   {cols}")
    finally:
        for p in servers:
            p.kill()

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: the phone and tablet frames are pixel-identical to the previous build, "
          "and the desktop frames differ (the guides are rendering).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
