#!/usr/bin/env python3
"""Census the outline across the whole built corpus, statically.

Answers the questions the card's D9/D10/D11/D16/D17 items need BEFORE any edit:
  * how many h2s sit in each structural position (page section / group label / brief title /
    inside the oral-answers section), across all 332 pages -- so a "level" assignment is
    derived from the corpus, not from one sample page;
  * how many `li` elements are genuinely outside a list (parent not OL/UL), which is the real
    D16 defect, as opposed to the looser `li:not(ol li)` count;
  * which pages use `.sec-head` / `.railhead`, and what the archive page's outline looks like.
"""
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "site", "dist")


class Census(HTMLParser):
    """Track open sections/articles so an h2's structural position is known."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []                 # (tag, classlist)
        self.h2 = {k: 0 for k in ("section", "group", "brief", "oral", "method", "other")}
        self.bare_li = 0                # <li> whose parent is not ol/ul
        self.li_total = 0
        self.sections = 0
        self.railhead = 0
        self.sec_head = 0
        self.dsec = 0
        self.li_after_ol_close = 0
        self._prev_close = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "li":
            self.li_total += 1
            parent = self.stack[-1][0] if self.stack else None
            if parent not in ("ol", "ul"):
                self.bare_li += 1
        if tag == "section":
            self.sections += 1
            if "dsec" in cls:
                self.dsec += 1
        if tag == "h2":
            if "railhead" in cls:
                self.h2["group"] += 1
                self.railhead += 1
            elif "sec-head" in cls:
                self.h2["section"] += 1
                self.sec_head += 1
            elif any(t == "article" and "brief" in c for t, c in self.stack):
                self.h2["brief"] += 1
            elif any(t == "section" and "oral" in c for t, c in self.stack):
                self.h2["oral"] += 1
            elif any(t == "section" and "method" in c for t, c in self.stack):
                self.h2["method"] += 1
            elif any(t == "section" for t, c in self.stack):
                self.h2["section"] += 1
            else:
                self.h2["other"] += 1
        if tag not in ("br", "img", "meta", "link", "input", "hr", "source", "area", "base",
                       "col", "embed", "param", "track", "wbr"):
            self.stack.append((tag, cls))

    def handle_endtag(self, tag):
        # pop to the matching open tag (defensive: generated HTML is well formed, but a
        # census that trusts it would silently mis-attribute headings if it were not)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def census(path):
    c = Census()
    c.feed(open(path, encoding="utf-8", errors="replace").read())
    src = open(path, encoding="utf-8", errors="replace").read()
    c.li_after_ol_close = len(re.findall(r"</ol>\s*<li", src))
    return c


def main():
    pages = sorted(f for f in os.listdir(os.path.join(DIST, "sittings")) if f.endswith(".html"))
    totals = {k: 0 for k in ("section", "group", "brief", "oral", "method", "other")}
    bare = li = secs = dsec = rh = sh = after = 0
    worst = []
    for f in pages:
        c = census(os.path.join(DIST, "sittings", f))
        for k in totals:
            totals[k] += c.h2[k]
        bare += c.bare_li
        li += c.li_total
        secs += c.sections
        dsec += c.dsec
        rh += c.railhead
        sh += c.sec_head
        after += c.li_after_ol_close
        if c.bare_li:
            worst.append((c.bare_li, f))
    print(f"pages                : {len(pages)}")
    print(f"<section> total      : {secs}   (.dsec: {dsec})")
    print(f"h2 by position       : {totals}")
    print(f"  .railhead          : {rh}")
    print(f"  .sec-head          : {sh}")
    print(f"li total             : {li}")
    print(f"li NOT inside ol/ul  : {bare}   (the true D16 count)")
    print(f"'</ol><li' sequences : {after}   (the looser count the audit used)")
    worst.sort(reverse=True)
    print(f"pages with a bare li : {len(worst)}  worst: {worst[:5]}")
    print()
    for name in ("index.html", "sittings/index.html"):
        p = os.path.join(DIST, name)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        print(f"--- {name}: h2={len(re.findall(r'<h2', src))} "
              f"section={len(re.findall(r'<section', src))} "
              f"railhead={len(re.findall(chr(34)+'railhead'+chr(34), src))} "
              f"sec-head={len(re.findall('sec-head', src))} "
              f"resume/pbar={'pbar' in src}/{'.resume' in src}")
        for m in re.finditer(r"<(h1|h2)[^>]*>(.{0,54})", src):
            print(f"      <{m.group(1)}> {re.sub('<[^>]+>', ' ', m.group(2)).strip()[:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
