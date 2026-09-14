"""Diff two uispec.py dumps: the mockup against web/next/.

    python tools/uidiff.py spec_mock.json spec_real.json

Prints one line per property that differs, grouped by landmark, with the
mockup's value first. Exit code is the number of differing properties, so the
build/verify/fix loop can run until it is 0.
"""
import json
import re
import sys
from pathlib import Path

A = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
B = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

# Properties whose exact value is not the point of the comparison: colors are
# the same tokens on both sides and are checked separately, and a box's own
# width is set by its content on a data-driven page.
# fontFamily is skipped because it is the DECLARED stack -- identical on both
# sides by construction, so it could never show that one side fell through to a
# substitute. It did: the design pulls Inter and JetBrains Mono from Google
# Fonts, the app shipped no copy of either, and every label in it was drawn in
# Segoe UI Variable and Consolas while this check reported zero differences.
# The face that actually renders is compared instead, under __fonts.
# `box` and gridTemplateRows are CONTENT, not design: the mockup holds nine
# hand-written calculations with longer names, a running row and a longer log,
# so comparing them measures the sample data, not the layout. What the design
# fixes — the rules — is everything else, and gridTemplateColumns is compared as
# a RATIO so a scrollbar on one side does not read as a different column split.
SKIP = {"fontFamily", "box", "gridTemplateRows"}
# Per-landmark exceptions, each with a reason. A plate's bottom margin is 16px
# unless it is the last thing in its section — which depends on whether that
# calculation produced a table to put under it, i.e. on the sample, not the
# design. (.plate:last-child{margin-bottom:0} is the rule doing it, and it is
# the same rule on both sides.)
PER_LANDMARK_SKIP = {
    # The mockup's first MLIP environment is ready (green). A seeded one
    # cannot be: readiness means an interpreter that actually imports a
    # backend, and nothing here installs one. The row itself is compared;
    # only the three colours its STATE picks are not.
    "envrow_badge": {"backgroundColor", "borderColor", "color"},
    "plate": {"marginBottom"},
    # Same story one rail down: .qgroup:last-child{margin-bottom:0}, and what
    # comes after the Opened-files group is the occupancy strip -- which the
    # mockup always draws and the real rail draws only while something is
    # running. The same rule, a different last child.
    "opened_group": {"marginBottom"},
    # The 20px above the section outline. A sticky top:0 sticks to the
    # scrollport's PADDING edge, so in the mockup — which never scrolls — the
    # container can hold that padding, and in a real report the content scrolls
    # through it above the chips. The 20px moved onto the bar (app.css,
    # "sticky outline"); it is the same 20px, on the other side of one edge.
    "res_content": {"paddingTop"},
    "secchips": {"paddingTop"},
    # repeat(auto-fit, minmax(120px, 1fr)) resolves to however many boxes fit
    # the children it has — the mockup lists six things to draw, a real result
    # lists what is actually in its folder. Same rule, different content.
    "viztypes": {"gridTemplateColumns"},
}
# Sizes within this many px read as the same to the eye and are not worth
# chasing; anything above it is a real difference in proportion.
TOL = 1.0

_num = re.compile(r"^-?\d+(?:\.\d+)?px$")


def same(a, b):
    if a == b:
        return True
    if isinstance(a, str) and isinstance(b, str) and _num.match(a) and _num.match(b):
        return abs(float(a[:-2]) - float(b[:-2])) <= TOL
    return False


def boxes_same(a, b):
    return all(abs(x - y) <= TOL for x, y in zip(a, b))


_px_list = re.compile(r"^[\d.]+px(?: [\d.]+px)+$")


def cols_same(a, b):
    """Two grid column lists are the same split when their RATIOS match."""
    if not (_px_list.match(a or "") and _px_list.match(b or "")):
        return a == b
    xa = [float(v[:-2]) for v in a.split()]
    xb = [float(v[:-2]) for v in b.split()]
    if len(xa) != len(xb):
        return False
    sa, sb = sum(xa), sum(xb)
    if sa <= 0 or sb <= 0:
        return False
    # 3%: a scrollbar is ~15px of a 550px rail and appears on whichever side
    # happens to hold more sample rows. That is not a different column split.
    return all(abs(x / sa - y / sb) <= 0.03 for x, y in zip(xa, xb))


n = 0
print("=" * 78)
print(f"{'MOCKUP':<38} vs  web/next/")
print("=" * 78)

for role in [k for k in A if not k.startswith("__")]:
    a, b = A.get(role), B.get(role)
    if a is None and b is None:
        print(f"\n{role}: absent on BOTH sides"); continue
    if a is None:
        print(f"\n{role}: !! not in the mockup"); continue
    if b is None:
        print(f"\n{role}: !! MISSING in web/next/"); n += 1; continue
    diffs = []
    for p in a:
        if p in SKIP or p in PER_LANDMARK_SKIP.get(role, ()):
            continue
        if p == "gridTemplateColumns":
            if not cols_same(a[p], b[p]):
                diffs.append((p, a[p], b[p]))
            continue
        if not same(a[p], b[p]):
            diffs.append((p, a[p], b[p]))
    if diffs:
        print(f"\n{role}")
        for p, x, y in diffs:
            print(f"   {p:<22} {str(x):<30} -> {y}")
        n += len(diffs)

ta, tb = A.get("__tokens", {}), B.get("__tokens", {})
tdiff = []
for k in sorted(set(ta) | set(tb)):
    if k in ("--font-sans", "--font-mono"):
        continue
    if ta.get(k, "").replace(" ", "") != tb.get(k, "").replace(" ", ""):
        tdiff.append((k, ta.get(k, "—"), tb.get(k, "—")))
if tdiff:
    print("\n__tokens")
    for k, x, y in tdiff:
        print(f"   {k:<22} {str(x):<30} -> {y}")
    n += len(tdiff)

# The faces the stacks actually resolve to (see the fontFamily note above).
# A width equal to that side's own `fallback` means the whole stack resolved to
# nothing and the page is drawing in the engine's default -- reported on its own
# line, because it is a different failure from "the two sides disagree" and has
# a different fix.
# What is ON each page, as opposed to how it looks. Reported but NOT counted:
# a class appears because the sample data called for it (a .badge.failed needs a
# failed row), so a difference here is a lead to follow, not a verdict. It is
# here because the landmark list is blind to structure -- it measures the
# properties of elements it names, so a control present on one side only
# changes nothing it looks at. That is how the Finished group's Clear button
# and the whole Opened-files group went missing without a single difference.
ca, cb = set(A.get("__classes", [])), set(B.get("__classes", []))
if ca and cb:
    only_a, only_b = sorted(ca - cb), sorted(cb - ca)
    if only_a or only_b:
        print()
        print("__structure  (advisory, not counted)")
        if only_a:
            print("   in the MOCKUP only :", ", ".join(only_a))
        if only_b:
            print("   in web/next/ only  :", ", ".join(only_b))

# The metric audit: every class COMBINATION both pages draw, compared on the
# box-model and type properties the stylesheet fixes. This is the answer to
# "a padding is a pixel off somewhere and the landmark list does not name that
# element" -- the landmarks are 121 selectors somebody chose, and a difference
# outside them is exactly as visible. Counted, because these are RULES: the two
# pages share one stylesheet, so a metric that differs means the markup put the
# element somewhere the rules read differently.
#
# Height is reported separately and NOT counted: a box's height is its content
# on a data-driven page, and the two sides hold different samples. It is here
# because a height change with no metric change is the signature worth chasing
# by hand (an extra child, a wrapped line).
STYLE_SKIP = {
    # Deliberate departures, each already explained where it was made:
    # the sticky outline moved 20px from the scrollport onto the bar (app.css).
    ".resmain > .rescontent": {"paddingTop"},
    ".rescontent > .secchips": {"paddingTop"},
}
sa, sb = A.get("__styles", {}), B.get("__styles", {})
if sa and sb:
    lines, hlines, ndiff = [], [], 0
    for key in sorted(set(sa) & set(sb)):
        x, y = sa[key], sb[key]
        skip = STYLE_SKIP.get(key, set())
        # alignItems / flexDirection / gap only mean something on a flex or grid
        # box. Chromium's UA stylesheet gives an <input> an align-items of its
        # own that varies with the input TYPE, so on everything else these
        # report a value nothing can see.
        flexish = "flex" in str(x.get("display")) or "grid" in str(x.get("display"))
        props = [p for p in sorted(x)
                 if p != "h" and p not in skip
                 and (flexish or p not in ("alignItems", "flexDirection", "gap"))
                 and not same(x.get(p), y.get(p))]
        if props:
            lines.append("   " + key)
            for prop in props:
                lines.append(f"      {prop:<20} {str(x.get(prop)):<28} -> {y.get(prop)}")
            ndiff += len(props)
        if abs(x.get("h", 0) - y.get("h", 0)) > 0.5:
            hlines.append(f"   {key:<60} height {x['h']:>8} -> {y['h']}")
    if lines:
        print()
        print("__styles  (metric differences)")
        for line in lines:
            print(line)
        n += ndiff
    if hlines:
        print()
        print(f"__styles heights  (advisory, not counted - {len(hlines)} of "
              f"{len(set(sa) & set(sb))} combinations)")
        for line in hlines:
            print(line)

fa, fb = A.get("__fonts"), B.get("__fonts")
if fa and fb:
    lines = []
    for side, f in (("MOCKUP", fa), ("web/next/", fb)):
        for k in ("sans", "mono"):
            if abs(f[k] - f["fallback"]) <= 0.01:
                lines.append(f"   !! {side}: --font-{k} resolves to NOTHING "
                             f"(drawing in the engine default)")
    for k in ("sans", "mono"):
        if abs(fa[k] - fb[k]) > TOL:
            lines.append(f"   {'--font-' + k:<22} {fa[k]:<30} -> {fb[k]}"
                         "   (px width of one 11px sample line)")
    if lines:
        print()
        print("__fonts")
        for line in lines:
            print(line)
        n += len(lines)

print("\n" + "=" * 78)
print("DIFFERENCES:", n)
sys.exit(min(n, 250))
