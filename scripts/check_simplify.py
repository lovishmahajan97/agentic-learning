#!/usr/bin/env python3
"""Check an example-simplification edit against a reference commit (default tag pre-simplify).

Passes when:
  - front matter is unchanged (status may change);
  - every section except Examples and Production patterns is unchanged (whitespace-insensitive);
  - Production patterns only gains lines (each old bullet is still there);
  - Examples still has >= 2 `###` examples, each code block (mermaid excluded) is <= 30 lines,
    and all example code in the lesson totals <= 60 lines.

Usage: check_simplify.py [--ref REF] FILES...   Exit 1 if any file fails.
"""
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402

MAX_BLOCK, MAX_TOTAL = 30, 60
FREE = ("Example", "Production patterns")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def sections(body):
    secs, _, _ = build.split_sections(body)
    return {build.norm_heading(h) or h: c for h, c in secs.items()}


def code_lines(md):
    return [len(b.strip().splitlines()) for lang, b in re.findall(r"```([^\n]*)\n(.*?)```", md, re.S)
            if lang.strip() != "mermaid"]


def check(old, new):
    probs = []
    ofm, obody, _ = build.split_front_matter(old)
    nfm, nbody, e = build.split_front_matter(new)
    if nfm is None:
        return ["no valid front matter"] + e
    for k in sorted(set(ofm) | set(nfm)):
        if k != "status" and ofm.get(k) != nfm.get(k):
            probs.append(f"front matter '{k}' changed")
    o, n = sections(obody), sections(nbody)
    for s in sorted(set(o) | set(n)):
        if s not in FREE and norm(o.get(s)) != norm(n.get(s)):
            probs.append(f"section '{s}' changed; only Examples and Production patterns may change")
    newpp = norm(n.get("Production patterns"))
    for line in (o.get("Production patterns") or "").splitlines():
        if line.strip().startswith(("- ", "* ")) and norm(line) not in newpp:
            probs.append(f"Production patterns bullet removed: {line.strip()[:70]!r}")
    ex = n.get("Example", "")
    if len(re.findall(r"^###\s", ex, re.M)) < 2:
        probs.append("Examples needs at least 2 ### examples")
    ls = code_lines(ex)
    for i, k in enumerate(ls, 1):
        if k > MAX_BLOCK:
            probs.append(f"example code block {i} has {k} lines (max {MAX_BLOCK})")
    if sum(ls) > MAX_TOTAL:
        probs.append(f"example code totals {sum(ls)} lines (max {MAX_TOTAL})")
    return probs


def main(argv):
    ref, files, i = "pre-simplify", [], 0
    while i < len(argv):
        if argv[i] == "--ref":
            ref = argv[i + 1]; i += 2
        else:
            files.append(argv[i]); i += 1
    bad = 0
    for f in files:
        p = Path(f) if Path(f).is_absolute() else ROOT / f
        rel = str(p.relative_to(ROOT))
        r = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], capture_output=True, text=True)
        if r.returncode:
            print(f"? {rel}: not in {ref}"); continue
        probs = check(r.stdout, p.read_text(encoding="utf-8"))
        if probs:
            bad += 1
            print(f"✗ {rel}")
            for x in probs:
                print(f"    - {x}")
        else:
            print(f"✓ {rel}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
