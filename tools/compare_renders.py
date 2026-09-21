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
"""
import base64
import io
import os
import subprocess
import sys
import tempfile
import time

ROOT = "/Users/shawnlin/parsnips/.worktrees/t_b764176e"
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
        time.sleep(0.8)
        c.eval(SETTLE)
        time.sleep(1.2)                 # let the pinned state apply and any rAF settle
        data = c.call("Page.captureScreenshot", format="png")["result"]["data"]
        state = c.eval("(() => JSON.stringify({y: Math.round(window.scrollY),"
                       " h: document.documentElement.scrollHeight,"
                       " pw: document.documentElement.clientWidth}))()")
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB"), state
    finally:
        c.close()


def changed_px(a, b):
    if a.size != b.size:
        return None
    d = ImageChops.difference(a, b)
    return sum(1 for px in d.getdata() if px != (0, 0, 0))


def main():
    orig_dir = tempfile.mkdtemp(prefix="parsnips-original-", dir="/tmp")
    print("preparing the previous-STYLESHEET tree in", orig_dir)
    prepare_old_tree(orig_dir)

    servers = []
    for port, directory in ((8477, os.path.join(ROOT, "site/dist")), (8478, orig_dir)):
        p = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                              "--bind", "127.0.0.1", "--directory", directory],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        servers.append(p)
    time.sleep(2.5)

    page = "sittings/2026-08-04.html"
    new_url = f"http://127.0.0.1:8477/{page}"
    old_url = f"http://127.0.0.1:8478/{page}"
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

        print(f"\n{'width':>6}  {'verdict':<30} changed px / total")
        for w, must_be_same in ((390, True), (759, True), (760, False),
                                (761, False), (1024, False), (1440, False)):
            a, sa = shot(old_url, w, 9983)
            b, sb = shot(new_url, w, 9984)
            if sa != sb:
                fails.append(f"{w}px: page state differs ({sa} vs {sb}) -- "
                             f"the comparison is not like-for-like")
            changed = changed_px(a, b)
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
            print(f"{w:>6}  {verdict:<30} {changed} / {total}")
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
