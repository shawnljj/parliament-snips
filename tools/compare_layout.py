"""Diff the audit's BEFORE measurements against the AFTER run, field by field.

The claim to test is "the guides were added and nothing else moved". The audit's own
measurements.json is the baseline, so this compares the two runs on every column and sticky
geometry value it recorded, and prints only the differences -- so "nothing else moved" is
demonstrated rather than asserted.

Usage: python3 tools/compare_layout.py docs/layout-audit/measurements.json docs/layout-audit/after/measurements.json
"""
import json
import sys

# The values that describe the LAYOUT (as opposed to screenshot timing, scroll offsets, etc).
GEOM_KEYS = ("x", "y", "w", "h", "right", "bottom", "position", "top", "zIndex",
             "gridTemplateColumns", "gap", "maxWidth", "width", "margin", "padding",
             "borderLeft", "borderRight", "display")
STICKY_KEYS = ("x", "right", "w", "position", "top", "right_css", "zIndex", "display")


def walk(a, b, path, out, keys):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((path + "." + str(k), a.get(k, "<absent>"), b.get(k, "<absent>")))
                continue
            walk(a[k], b[k], path + "." + str(k), out, keys)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path + ".len", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", out, keys)
    else:
        if a != b:
            out.append((path, a, b))


def section(d, name, keys):
    out = []
    for w in sorted(d.get("widths", {}), key=int):
        for state in ("at_top", "scrolled"):
            block = (d["widths"][w].get(state) or {}).get(name) or {}
            out.append((w, state, block))
    return out


def main():
    before_p, after_p = sys.argv[1], sys.argv[2]
    b = json.load(open(before_p, encoding="utf-8"))
    a = json.load(open(after_p, encoding="utf-8"))
    print(f"BEFORE {before_p}  url={b.get('url')}")
    print(f"AFTER  {after_p}  url={a.get('url')}")
    # Compare the PAGE, not the origin: a second local server on another port serves the same
    # page, and a port difference is not a difference in what was measured.
    from urllib.parse import urlparse
    pb, pa = urlparse(b.get("url") or ""), urlparse(a.get("url") or "")
    if pb.path != pa.path:
        print(f"  WARNING: different pages were measured ({pb.path} vs {pa.path})")
    print()

    diffs = []
    for name, keys in (("columns", GEOM_KEYS), ("sticky", STICKY_KEYS)):
        for w in sorted(set(b.get("widths", {})) | set(a.get("widths", {})), key=int):
            for state in ("at_top", "scrolled"):
                x = ((b.get("widths", {}).get(w) or {}).get(state) or {}).get(name)
                y = ((a.get("widths", {}).get(w) or {}).get(state) or {}).get(name)
                if x is None or y is None:
                    if x is not y:
                        diffs.append((f"{name} w={w} {state}", "present" if x else "absent",
                                      "present" if y else "absent"))
                    continue
                walk(x, y, f"{w}px/{state}/{name}", diffs, keys)

    print(f"{len(diffs)} measured difference(s):\n")
    # Group by width for readability.
    for d in diffs:
        path, x, y = d
        marker = ""
        print(f"  {path}\n      before={x!r}\n      after ={y!r}{marker}")

    # The one thing that MUST differ: the guides exist. Call that out explicitly.
    print()
    print("Guides are not in the audit's field set (they did not exist then), so their "
          "presence is asserted by tools/check_column_guides.py instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
