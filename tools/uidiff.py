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

# Properties whose exact value is not the point of the comparison: fonts differ
# by machine, colors are the same tokens on both sides and are checked
# separately, and a box's own width is set by its content on a data-driven page.
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
PER_LANDMARK_SKIP = {"plate": {"marginBottom"}}
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

print("\n" + "=" * 78)
print("DIFFERENCES:", n)
sys.exit(min(n, 250))
