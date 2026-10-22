#!/usr/bin/env python3
"""No-loss check for the crisp rewrite: compare a lesson with its committed version.

A rewrite may restructure prose into bullets, steps and tables and may ADD material, but it must not
lose facts. This fails when, relative to the reference version (default: tag pre-crisp, the commit before the rewrite):
  - front matter changed (other than `status`);
  - the Role lens, Check yourself or Quiz sections changed (whitespace-insensitive);
  - an original fenced code block is missing (whitespace-insensitive);
  - a number from the original prose is missing (e.g. 0.5, 4,000, 2x, 95%);
  - an inline `code` span from the original prose is missing.

Usage:
  compare_lesson.py content/<module>/<slug>.md [...]   # compare with tag pre-crisp (or HEAD if absent)
  compare_lesson.py --ref <git-ref> FILES...           # compare with another commit
  compare_lesson.py --all [--ref REF]                  # every lesson stamped `status: crisp`
Exit 1 if any file loses data.
"""
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402

KEEP_SECTIONS = ("Role lens", "Check yourself", "Quiz")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def sections(body):
    secs, _, _ = build.split_sections(body)
    out = {}
    for h, c in secs.items():
        out[build.norm_heading(h) or h] = c
    return out


def code_blocks(md):
    return [norm(b) for b in re.findall(r"```[^\n]*\n(.*?)```", md, flags=re.S)]


def prose(md):
    return re.sub(r"```.*?```", " ", md, flags=re.S)


NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s?(%|x|×))?(?![\w])")


def numbers(md):
    out = set()
    for m in NUM.finditer(prose(md)):
        out.add(m.group(1).replace(",", ""))
    return out


def inline_code(md):
    return {norm(x) for x in re.findall(r"(?<!`)`([^`]+)`(?!`)", prose(md))}   # spans may wrap lines


def compare(old_text, new_text):
    problems = []
    ofm, obody, _ = build.split_front_matter(old_text)
    nfm, nbody, e = build.split_front_matter(new_text)
    if nfm is None:
        return ["new file has no valid front matter"] + e
    if ofm is None:
        return []
    agentic = str(nfm.get("status", "")).lower().startswith("agentic")
    for k in sorted(set(ofm) | set(nfm)):
        if k == "status" or (agentic and k == "agent_relevance"):
            continue
        if ofm.get(k) != nfm.get(k):
            problems.append(f"front matter '{k}' changed: {ofm.get(k)!r} -> {nfm.get(k)!r}")
    osec, nsec = sections(obody), sections(nbody)
    for s in KEEP_SECTIONS:
        if norm(osec.get(s)) != norm(nsec.get(s)):
            problems.append(f"section '{s}' must stay unchanged")
    old_rest = "\n".join(v for k, v in osec.items() if k not in KEEP_SECTIONS)
    new_rest = "\n".join(v for k, v in nsec.items() if k not in KEEP_SECTIONS)
    new_code = set(code_blocks(new_rest))
    new_code_joined = " ".join(new_code)
    for b in ([] if agentic else code_blocks(old_rest)):   # agentic reframe may swap generic code for agent code
        if b not in new_code and b not in new_code_joined:
            problems.append(f"code block lost: {b[:70]!r}")
    new_flat = norm(new_rest).replace(",", "")
    new_nums = numbers(new_rest) | set(re.findall(r"\d+(?:\.\d+)?", new_flat))
    lost = sorted(numbers(old_rest) - new_nums, key=lambda x: (len(x), x))
    if lost:
        problems.append(f"numbers lost from prose: {', '.join(lost[:15])}")
    new_inline = inline_code(new_rest) | {norm(x) for x in re.findall(r"`([^`]+)`", new_rest)}
    squash = lambda x: re.sub(r"\s+", "", x)
    new_sq = squash(new_rest)
    lost_ic = sorted(x for x in inline_code(old_rest) - new_inline if squash(x) not in new_sq)
    if lost_ic:
        problems.append(f"inline code lost: {', '.join(repr(x) for x in lost_ic[:10])}")
    return problems


def ref_text(ref, rel):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main(argv):
    has_tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", "refs/tags/pre-crisp"],
                             capture_output=True).returncode == 0
    has_agentic = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", "refs/tags/pre-agentic"],
                                 capture_output=True).returncode == 0
    ref, files, all_mode = ("pre-agentic" if has_agentic else "pre-crisp" if has_tag else "HEAD"), [], False
    i = 0
    while i < len(argv):
        if argv[i] == "--ref":
            ref = argv[i + 1]; i += 2
        elif argv[i] == "--all":
            all_mode = True; i += 1
        else:
            files.append(argv[i]); i += 1
    if all_mode:
        for p in sorted((ROOT / "content").glob("*/*.md")):
            if p.name.startswith("_"):
                continue
            if re.search(r"^status:\s*(crisp|agentic)(-verified)?\s*$", p.read_text(encoding="utf-8")[:2000], re.M):
                files.append(str(p.relative_to(ROOT)))
    bad = 0
    for f in files:
        p = Path(f) if Path(f).is_absolute() else ROOT / f
        rel = str(p.relative_to(ROOT))
        old = ref_text(ref, rel)
        if old is None:
            print(f"? {rel}: not in {ref}, nothing to compare"); continue
        probs = compare(old, p.read_text(encoding="utf-8"))
        if probs:
            bad += 1
            print(f"✗ {rel}")
            for x in probs:
                print(f"    - {x}")
        else:
            print(f"✓ {rel} (nothing lost vs {ref})")
    if all_mode:
        print(f"checked {len(files)} crisp lessons against {ref}; {bad} lost data")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
