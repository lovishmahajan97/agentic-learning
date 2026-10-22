#!/usr/bin/env python3
"""Parse lesson Markdown (content/<module>/<slug>.md) + module files (_module.md),
validate them against the taxonomy and the authoring spec, and bundle site/data.js.

Usage:
  build.py                      build site/data.js and print a coverage report
  build.py --check f1.md [...]  validate specific files (exit 1 on any error)
  build.py --check-module <id>  validate every file of one module
  build.py --report             coverage/quality report only
"""
import json, re, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
TAX = json.loads((ROOT / "taxonomy.json").read_text())
MODULES = {m["id"]: m for m in TAX["modules"]}
ALL_KEYS = {t["key"]: (m["id"], t) for m in TAX["modules"] for t in m["topics"]}

KINDS = {"concept", "tool", "metric", "pattern", "practice", "decision"}
DIFFS = {"intro", "core", "advanced"}
TOPIC_SECTIONS = ["TL;DR", "Why it exists", "How it works", "Example", "Trade-offs", "Pitfalls",
                  "When to use", "Role lens", "Check yourself", "Quiz"]
RELEVANCE = {"core", "useful", "background"}
MASTERY = {"understand", "implement", "evaluate"}
CODE_TESTED = {"yes", "partial", "no"}
V4_FORCE = False          # set by --v4: check against the mastery format (SPEC v4)
V3_FORCE = False          # set by --v3: check against the agent-builder format (SPEC v3)
TOPIC_SECTIONS_V2 = ["TL;DR", "Why it exists", "How it works", "Diagram", "Example", "Production patterns",
                     "Trade-offs", "Pitfalls", "When to use", "Role lens", "Check yourself",
                     "Interview questions", "Quiz"]
MERMAID_KINDS = ("flowchart", "graph", "sequenceDiagram", "stateDiagram", "stateDiagram-v2", "classDiagram",
                 "erDiagram", "timeline", "gantt", "pie", "mindmap", "quadrantChart")
V2_FORCE = False          # set by --v2: check files against the crisp format even before they are stamped
PARA_MAX, BULLET_MAX, TLDR_MAX_V2 = 60, 80, 45
MODULE_SECTIONS = ["Overview", "Why it matters", "Mental model", "Suggested order", "Capstone"]

# ---------------------------------------------------------------- helpers
def norm_heading(h):
    h = h.strip().rstrip(":").strip().lower().replace("’", "'")
    h = re.sub(r"\s+", " ", h)
    aliases = {"tl;dr": "TL;DR", "tldr": "TL;DR", "summary": "TL;DR",
               "why it exists": "Why it exists", "why": "Why it exists",
               "how it works": "How it works", "how": "How it works",
               "example": "Example", "examples": "Example", "worked example": "Example",
               "trade-offs": "Trade-offs", "tradeoffs": "Trade-offs", "trade offs": "Trade-offs",
               "pitfalls": "Pitfalls", "common pitfalls": "Pitfalls",
               "production patterns": "Production patterns", "in production": "Production patterns",
               "exercise": "Exercise", "hands-on exercise": "Exercise",
               "mastery test": "Mastery test",
               "use cases & projects": "Use cases & projects", "use cases and projects": "Use cases & projects",
               "for agent builders": "For agent builders", "agent builder's take": "For agent builders",
               "diagram": "Diagram", "flowchart": "Diagram", "flow": "Diagram",
               "interview questions": "Interview questions", "interview prep": "Interview questions",
               "when to use": "When to use", "when to use / decide": "When to use", "when to use it": "When to use",
               "when to use / when not to": "When to use", "when": "When to use",
               "role lens": "Role lens", "check yourself": "Check yourself", "quiz": "Quiz",
               "overview": "Overview", "why it matters": "Why it matters", "mental model": "Mental model",
               "suggested order": "Suggested order", "capstone": "Capstone",
               "brief": "Brief", "deliverables": "Deliverables", "rubric": "Rubric"}
    return aliases.get(h)

def split_front_matter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text, ["missing front matter block (file must start with '---')"]
    fm, i = {}, 1
    cur = None
    while i < len(lines) and lines[i].strip() != "---":
        ln = lines[i]
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if m and not ln.startswith((" ", "\t")):
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                fm[key] = []; cur = key
            else:
                if val.startswith("[") and val.endswith("]"):
                    fm[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
                else:
                    fm[key] = val.strip("'\"")
                cur = None
        elif re.match(r"^\s+-\s+", ln) and cur is not None:
            fm[cur].append(re.sub(r"^\s+-\s+", "", ln).strip().strip("'\""))
        elif ln.strip() == "":
            pass
        else:
            return None, text, [f"front matter line {i+1} not understood: {ln!r}"]
        i += 1
    if i >= len(lines):
        return None, text, ["front matter never closed with '---'"]
    return fm, "\n".join(lines[i+1:]), []

def split_sections(body, level=2):
    """Split markdown into {heading: content} by headings of `level`, fence-aware."""
    marker = "#" * level + " "
    secs, order, cur, buf, in_fence = {}, [], None, [], False
    pre = []
    for ln in body.splitlines():
        if re.match(r"^\s*(```|~~~)", ln):
            in_fence = not in_fence
        if not in_fence and ln.startswith(marker):
            if cur is not None:
                secs[cur] = "\n".join(buf).strip()
            cur, buf = ln[len(marker):].strip(), []
            order.append(cur)
        elif cur is None:
            pre.append(ln)
        else:
            buf.append(ln)
    if cur is not None:
        secs[cur] = "\n".join(buf).strip()
    return secs, order, "\n".join(pre).strip()

def strip_code(md):
    return re.sub(r"```.*?```", " ", md, flags=re.S)

def words(md):
    return len(re.findall(r"\b\w+\b", strip_code(md)))

def bullets(md):
    """Collect top-level '- ' / '* ' bullets, joining indented continuation lines."""
    out, in_fence = [], False
    for ln in md.splitlines():
        if re.match(r"^\s*(```|~~~)", ln):
            in_fence = not in_fence
        if in_fence:
            if out: out[-1] += "\n" + ln
            continue
        m = re.match(r"^[-*]\s+(.*)$", ln)
        if m:
            out.append(m.group(1).strip())
        elif out and ln.strip() and (ln.startswith((" ", "\t")) or not re.match(r"^\d+\.", ln)):
            out[-1] += " " + ln.strip()
    return [b for b in out if b]

def long_blocks(md, para_max=PARA_MAX, bullet_max=BULLET_MAX):
    """Prose paragraphs over para_max words and list items over bullet_max words (code, tables,
    headings and quotes excluded). Returns a list of (kind, words, first 50 chars)."""
    out, in_fence, para, item = [], False, [], []
    def flush_para():
        if para:
            w = words("\n".join(para))
            if w > para_max: out.append(("paragraph", w, " ".join(para)[:50]))
            para.clear()
    def flush_item():
        if item:
            w = words("\n".join(item))
            if w > bullet_max: out.append(("bullet", w, " ".join(item)[:50]))
            item.clear()
    for ln in md.splitlines():
        if re.match(r"^\s*(```|~~~)", ln):
            in_fence = not in_fence; flush_para(); continue
        if in_fence: continue
        st = ln.strip()
        if not st:
            flush_para(); continue
        if re.match(r"^([-*+]|\d+[.)])\s+", st) and not ln.startswith((" " * 4, "\t")):
            flush_para(); flush_item(); item.append(st); continue
        if st.startswith(("#", "|", ">")):
            flush_para(); flush_item(); continue
        if item and (ln.startswith((" ", "\t"))):
            item.append(st); continue
        flush_item(); para.append(st)
    flush_para(); flush_item()
    return out

def diagram(md, errs):
    m = re.search(r"```mermaid[^\n]*\n(.*?)```", md, re.S)
    if not m:
        errs.append("'Diagram' needs a ```mermaid fenced block"); return None
    code = m.group(1).strip("\n")
    first = next((l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("%%")), "")
    if not first.split(" ")[0] in MERMAID_KINDS:
        errs.append(f"'Diagram': first mermaid line must start with one of {MERMAID_KINDS[:4]}... (found {first[:30]!r})")
    if code.count("\n") > 40: errs.append("'Diagram' too large (max ~40 lines); keep it to the core flow")
    for bad in (";", "classDef", "click ", "style "):
        if bad in code: errs.append(f"'Diagram': avoid {bad.strip()!r} (keeps the diagram portable and parseable)")
    caption = (md[:m.start()] + md[m.end():]).strip()
    if words(caption) < 8: errs.append("'Diagram' needs a one or two sentence caption saying what to look at")
    return {"code": code, "caption": caption}

def mastery_block(fm, got, errs):
    lvl = str(fm.get("mastery", "")).lower()
    if lvl not in MASTERY: errs.append(f"front matter mastery must be one of {sorted(MASTERY)} (the level this lesson should take you to)")
    ct = str(fm.get("code_tested", "")).lower()
    if ct not in CODE_TESTED: errs.append(f"front matter code_tested must be one of {sorted(CODE_TESTED)}")
    pre = fm.get("prerequisites", [])
    if isinstance(pre, str): pre = [pre]
    badp = [x for x in pre if x not in ALL_KEYS]
    if badp: errs.append(f"prerequisites not in taxonomy: {badp}")
    if len(pre) > 5: errs.append("prerequisites: at most 5 keys")
    ex = got["Exercise"]
    if words(ex) < 80: errs.append(f"'Exercise' too thin ({words(ex)} words, min 80)")
    for lab in ("**Goal", "**Acceptance"):
        if lab.lower() not in ex.lower(): errs.append(f"'Exercise' needs a bold {lab.strip('*')} line")
    if "```" in ex and ex.count("```") >= 2 and words(re.sub(r"```.*?```", "", ex, flags=re.S)) < 60:
        errs.append("'Exercise' must not be a worked solution: describe the task and checks, give at most a starter stub")
    mt = got["Mastery test"]
    if words(mt) < 60: errs.append(f"'Mastery test' too thin ({words(mt)} words, min 60)")
    for lab in ("**Problem", "**A strong answer"):
        if lab.lower() not in mt.lower(): errs.append(f"'Mastery test' needs a bold {lab.strip('*')} part")
    diag = [b for b in bullets(got["Pitfalls"]) if "diagnos" in b.lower()]
    if len(diag) < 3: errs.append(f"'Pitfalls' (failure modes): at least 3 bullets must say how to diagnose (found {len(diag)})")
    for sec in ("Exercise", "Mastery test"):
        for kind_, w, head in long_blocks(got[sec]):
            errs.append(f"'{sec}': {kind_} of {w} words; split it: {head!r}")
    ms = fm.get("milestones", [])
    if isinstance(ms, str): ms = [ms]
    out_ms = []
    for x in ms:
        mid_, _, title = x.partition(":")
        if mid_.strip() not in MODULES: errs.append(f"milestone '{x}' must start with a module id and a colon")
        else: out_ms.append({"after": mid_.strip(), "title": title.strip()})
    return {"level": lvl, "code_tested": ct, "prerequisites": [x for x in pre if x in ALL_KEYS],
            "exercise": ex, "test": mt, "milestones": out_ms}

def projects(md, errs):
    secs, order, _ = split_sections(md, level=3)
    by = {re.sub(r"\s+", " ", h.strip().lower()): secs[h] for h in order}
    uc = next((v for k, v in by.items() if k.startswith("use case")), None)
    mp = next((v for k, v in by.items() if k.startswith("mini project")), None)
    if uc is None or len(bullets(uc)) < 3:
        errs.append("'Use cases & projects' needs '### Use cases' with 3-5 bold-led bullets")
    if mp is None or words(mp) < 60:
        errs.append("'Use cases & projects' needs '### Mini project' (60+ words: goal, steps, done when)")
    for kind_, w, head in long_blocks(md):
        errs.append(f"'Use cases & projects': {kind_} of {w} words; split it: {head!r}")
    return {"use_cases": uc or "", "mini_project": mp or ""}

def interview(md, errs):
    secs, order, _ = split_sections(md, level=3)
    qs = []
    for h in order:
        q = re.sub(r"^(Q\d*[.:)]?\s*)", "", h.strip(), flags=re.I).strip()
        a = secs[h].strip()
        if words(q) < 6: errs.append(f"Interview question too short: {q[:50]!r}")
        if words(a) < 25: errs.append(f"Interview answer too short (min 25 words) for: {q[:50]!r}")
        qs.append({"q": q, "a": a})
    if not 4 <= len(qs) <= 6:
        errs.append(f"'Interview questions' needs 4-6 questions as '### question' headings with answers (found {len(qs)})")
    return qs

def role_lens(md, errs, where):
    res = {}
    for b in bullets(md):
        m = re.match(r"^\*\*(engineer|ai engineer|architect|ai architect|fde)\s*:?\*\*\s*:?\s*(.*)$", b, re.I | re.S)
        if m:
            k = m.group(1).lower()
            k = "engineer" if "engineer" in k else ("architect" if "architect" in k else "fde")
            res[k] = m.group(2).strip()
    for k in ("engineer", "architect", "fde"):
        if k not in res or words(res[k]) < 12:
            errs.append(f"{where}: needs a '- **{k.title() if k!='fde' else 'FDE'}:** ...' bullet of at least 12 words")
    return res

def check_yourself(md, errs):
    m = re.search(r"\*\*Q:?\*\*:?\s*(.*?)\s*\*\*A:?\*\*:?\s*(.*)$", md, re.S | re.I)
    if not m:
        errs.append("Check yourself: must contain '**Q:** ...' followed by '**A:** ...'")
        return {"q": "", "a": ""}
    q, a = m.group(1).strip(), m.group(2).strip()
    if words(q) < 8: errs.append("Check yourself: question too short (min 8 words)")
    if words(a) < 60: errs.append(f"Check yourself: answer too short ({words(a)} words, min 60)")
    return {"q": q, "a": a}

def quiz(md, errs):
    parts = re.split(r"^###\s+", md, flags=re.M)
    qs = []
    for p in parts[1:]:
        lines = p.splitlines()
        qtext = re.sub(r"^(Q\d*|Question\s*\d*)\s*[:.)-]\s*", "", lines[0].strip(), flags=re.I)
        opts, correct, expl, qextra = [], [], [], []
        for ln in lines[1:]:
            m = re.match(r"^\s*[-*]\s*\[( |x|X)\]\s*(.*)$", ln)
            if m:
                if m.group(1).lower() == "x": correct.append(len(opts))
                opts.append(m.group(2).strip())
            elif ln.strip().startswith(">"):
                expl.append(re.sub(r"^\s*>\s?", "", ln))
            elif expl and ln.strip():
                expl.append(ln.strip())
            elif ln.strip() and not opts:
                qextra.append(ln.strip())
        if qextra: qtext = (qtext + " " + " ".join(qextra)).strip()
        e = " ".join(x.strip() for x in expl).strip()
        e = re.sub(r"^\*{0,2}explanation\*{0,2}\s*:\s*", "", e, flags=re.I)
        n = len(qs) + 1
        if len(opts) != 4: errs.append(f"Quiz Q{n}: needs exactly 4 options (found {len(opts)})")
        if len(correct) != 1: errs.append(f"Quiz Q{n}: exactly one option must be marked [x] (found {len(correct)})")
        if words(e) < 15: errs.append(f"Quiz Q{n}: explanation ('> ...') too short (min 15 words)")
        if words(qtext) < 5: errs.append(f"Quiz Q{n}: question too short")
        qs.append({"q": qtext, "options": opts, "answer": correct[0] if correct else -1, "explanation": e})
    if len(qs) != 3:
        errs.append(f"Quiz: needs exactly 3 questions as '### Q: ...' blocks (found {len(qs)})")
    return qs

# ---------------------------------------------------------------- topic
def parse_topic(path: Path):
    errs, warns = [], []
    text = path.read_text(encoding="utf-8")
    fm, body, e = split_front_matter(text); errs += e
    if fm is None:
        return None, errs, warns
    mid, sl = path.parent.name, path.stem
    key = f"{mid}/{sl}"
    if key not in ALL_KEYS:
        errs.append(f"{key} is not a topic in taxonomy.json (check the file name and folder)")
        return None, errs, warns
    tax_t = ALL_KEYS[key][1]
    if fm.get("id") != sl: errs.append(f"front matter id must be '{sl}'")
    if fm.get("module") != mid: errs.append(f"front matter module must be '{mid}'")
    if not fm.get("name"): errs.append("front matter needs name")
    kind = fm.get("kind", "")
    if kind not in KINDS: errs.append(f"front matter kind must be one of {sorted(KINDS)}")
    diff = fm.get("difficulty", "core")
    if diff not in DIFFS: errs.append(f"difficulty must be one of {sorted(DIFFS)}")
    try:
        est = int(fm.get("est_minutes", 12))
    except ValueError:
        est, _ = 12, errs.append("est_minutes must be an integer")
    related = fm.get("related", [])
    if isinstance(related, str): related = [related]
    bad = [r for r in related if r not in ALL_KEYS]
    if bad: errs.append(f"related keys not in taxonomy: {bad}")
    if r"{}".format(key) in related: errs.append("related must not include the topic itself")
    if len([r for r in related if r in ALL_KEYS]) < 2: errs.append("related: give at least 2 valid '<module>/<slug>' keys")
    sources = fm.get("sources", [])
    if isinstance(sources, str): sources = [sources]

    secs, order, pre = split_sections(body)
    got = {}
    for h, c in secs.items():
        nh = norm_heading(h)
        if nh is None: errs.append(f"unknown '## {h}' section; allowed: {TOPIC_SECTIONS_V2}")
        elif nh in got: errs.append(f"duplicate section '{nh}'")
        else: got[nh] = c
    st_ = str(fm.get("status", "")).lower()
    v4 = V4_FORCE or st_.startswith("mastery")
    v3 = v4 or V3_FORCE or st_.startswith("agentic")
    v2 = v3 or V2_FORCE or st_.startswith("crisp") or "Production patterns" in got
    need = (["For agent builders"] + TOPIC_SECTIONS_V2 + (["Exercise", "Mastery test"] if v4 else []) + ["Use cases & projects"]) if v3 else (TOPIC_SECTIONS_V2 if v2 else TOPIC_SECTIONS)
    for s in need:
        if s not in got: errs.append(f"missing section '## {s}'")
    if any(s not in got for s in need):
        return None, errs, warns
    is_tool = kind == "tool"
    patterns, dia, ivq, proj, mastery = [], None, [], None, None
    if not v2:
        mins = {"TL;DR": 15, "Why it exists": 120, "How it works": 150 if is_tool else 250,
                "When to use": 60}
        for s, mn in mins.items():
            w = words(got[s])
            if w < mn: errs.append(f"'{s}' too short: {w} words (min {mn})")
        if words(got["TL;DR"]) > 90: errs.append("'TL;DR' too long (max 90 words)")
        if "```" in got["Example"]:
            if words(got["Example"]) < 20:
                errs.append("'Example' needs 20+ words of prose around the code block explaining what it shows")
        elif words(got["Example"]) < 120:
            errs.append("'Example' needs either a fenced code block or a worked scenario of 120+ words")
    else:
        # ---- crisp format (SPEC v2)
        tw = words(got["TL;DR"])
        if tw < 10 or tw > TLDR_MAX_V2: errs.append(f"'TL;DR' must be 10-{TLDR_MAX_V2} words (found {tw})")
        if len(bullets(got["Why it exists"])) < 3: errs.append("'Why it exists' needs 3-5 bullets")
        if words(got["Why it exists"]) < 50: errs.append("'Why it exists' too thin (min 50 words)")
        hm = 80 if is_tool else 120
        if words(got["How it works"]) < hm: errs.append(f"'How it works' too thin (min {hm} words)")
        ex, _, _ = split_sections(got["Example"], level=3)
        if len(ex) < 2: errs.append(f"'Examples' needs at least 2 examples, each under its own '### title' (found {len(ex)})")
        if "```" not in got["Example"]: errs.append("'Examples' needs at least one fenced code block")
        patterns = bullets(got["Production patterns"])
        if len(patterns) < 3: errs.append(f"'Production patterns' needs 3-6 bullets (found {len(patterns)})")
        for b in patterns:
            if not b.startswith("**"): errs.append(f"production pattern must start with a **bold name**: {b[:50]!r}")
            if words(b) < 15: errs.append(f"production pattern too thin (min 15 words): {b[:50]!r}")
        if len(bullets(got["When to use"])) < 3: errs.append("'When to use' needs bullets (use when / avoid when / measure)")
        dia = diagram(got["Diagram"], errs)
        if v3:
            rel = str(fm.get("agent_relevance", "")).lower()
            if rel not in RELEVANCE: errs.append(f"front matter agent_relevance must be one of {sorted(RELEVANCE)}")
            proj = projects(got["Use cases & projects"], errs)
            if v4:
                mastery = mastery_block(fm, got, errs)
            fb = bullets(got["For agent builders"])
            if len(fb) < 3: errs.append("'For agent builders' needs 3-5 bold-led bullets (why it matters for agents, where it shows up in an agent stack, what you can skip)")
            if words(got["For agent builders"]) < 50: errs.append("'For agent builders' too thin (min 50 words)")
            for kind_, w, head in long_blocks(got["For agent builders"]):
                errs.append(f"'For agent builders': {kind_} of {w} words; split it: {head!r}")
        ivq = interview(got["Interview questions"], errs)
        for sec in ("TL;DR", "Why it exists", "How it works", "Diagram", "Example", "Production patterns",
                    "Trade-offs", "Pitfalls", "When to use", "Interview questions"):
            for kind_, w, head in long_blocks(got[sec]):
                lim = PARA_MAX if kind_ == "paragraph" else BULLET_MAX
                errs.append(f"'{sec}': {kind_} of {w} words (max {lim}); split it: {head!r}")
    tr, pf = bullets(got["Trade-offs"]), bullets(got["Pitfalls"])
    if len(tr) < 3: errs.append(f"'Trade-offs' needs at least 3 bullets (found {len(tr)})")
    if len(pf) < 3: errs.append(f"'Pitfalls' needs at least 3 bullets (found {len(pf)})")
    for b in tr + pf:
        if words(b) < 8: errs.append(f"bullet too thin (min 8 words): {b[:60]!r}")
    if "TODO" in text or "TBD" in text or "lorem" in text.lower():
        errs.append("placeholder text (TODO/TBD/lorem) found")
    t = {"id": sl, "key": key, "module": mid, "name": fm.get("name") or tax_t["name"],
         "taxonomy_name": tax_t["name"], "kind": kind, "difficulty": diff, "est_minutes": est,
         "related": [r for r in related if r in ALL_KEYS], "sources": sources,
         "tldr": got["TL;DR"], "why": got["Why it exists"], "how": got["How it works"],
         "example": got["Example"], "patterns": patterns, "agent": got.get("For agent builders") if v3 else None,
         "agent_relevance": str(fm.get("agent_relevance", "")).lower() or None, "projects": proj,
         "mastery": {k: v for k, v in mastery.items() if k not in ("exercise", "test")} if mastery else None,
         "exercise": mastery["exercise"] if mastery else None, "mastery_test": mastery["test"] if mastery else None, "diagram": dia, "interview": ivq, "format": "crisp" if v2 else "long", "tradeoffs": tr, "pitfalls": pf, "when": got["When to use"],
         "role_lens": role_lens(got["Role lens"], errs, "Role lens"),
         "check": check_yourself(got["Check yourself"], errs), "quiz": quiz(got["Quiz"], errs)}
    if "caveat" in tax_t: t["caveat"] = tax_t["caveat"]
    if "note" in tax_t: t["note"] = tax_t["note"]
    if "status" in fm: t["review"] = fm["status"]
    return t, errs, warns

# ---------------------------------------------------------------- module
def parse_module(path: Path):
    errs = []
    text = path.read_text(encoding="utf-8")
    fm, body, e = split_front_matter(text); errs += e
    if fm is None: return None, errs
    mid = path.parent.name
    if mid not in MODULES: errs.append(f"{mid} not a module"); return None, errs
    if fm.get("id") != mid: errs.append(f"front matter id must be '{mid}'")
    secs, order, pre = split_sections(body)
    got = {}
    for h, c in secs.items():
        nh = norm_heading(h)
        if nh is None: errs.append(f"unknown '## {h}' section; allowed: {MODULE_SECTIONS}")
        else: got[nh] = c
    for s in MODULE_SECTIONS:
        if s not in got: errs.append(f"missing section '## {s}'")
    if any(s not in got for s in MODULE_SECTIONS): return None, errs
    if words(got["Overview"]) < 150: errs.append(f"Overview too short ({words(got['Overview'])} words, min 150)")
    if words(got["Mental model"]) < 100: errs.append(f"Mental model too short (min 100 words)")
    why = role_lens(got["Why it matters"], errs, "Why it matters")
    lines = [b.strip() for b in re.findall(r"^\s*\d+[.)]\s*(.+)$", got["Suggested order"], re.M)]
    slugs, reasons = [], {}
    for ln in lines:
        parts = re.split(r"\s+[—–-]\s+|:\s+", ln.strip(), maxsplit=1)
        sl = parts[0].strip().strip("`*").strip()
        slugs.append(sl)
        if len(parts) > 1 and parts[1].strip(): reasons[sl] = parts[1].strip()
    want = {t["id"] for t in MODULES[mid]["topics"]}
    if set(slugs) != want or len(slugs) != len(want):
        errs.append(f"Suggested order must list every topic slug of the module exactly once. "
                    f"missing={sorted(want-set(slugs))} extra={sorted(set(slugs)-want)}")
    cap, corder, _ = split_sections(got["Capstone"], level=3)
    capn = {norm_heading(h) or h: c for h, c in cap.items()}
    for s in ("Brief", "Deliverables", "Rubric"):
        if s not in capn: errs.append(f"Capstone needs '### {s}'")
    if any(s not in capn for s in ("Brief", "Deliverables", "Rubric")): return None, errs
    if words(capn["Brief"]) < 120: errs.append("Capstone brief too short (min 120 words)")
    if V2_FORCE or str(fm.get("status", "")).lower().startswith(("crisp", "agentic", "mastery")):
        for sec, md_ in (("Overview", got["Overview"]), ("Mental model", got["Mental model"]),
                         ("Capstone brief", capn["Brief"])):
            for kind_, w, head in long_blocks(md_):
                lim = PARA_MAX if kind_ == "paragraph" else BULLET_MAX
                errs.append(f"'{sec}': {kind_} of {w} words (max {lim}); split it: {head!r}")
    dl, rb = bullets(capn["Deliverables"]), bullets(capn["Rubric"])
    if len(dl) < 3: errs.append("Capstone deliverables: at least 3 bullets")
    if len(rb) < 3: errs.append("Capstone rubric: at least 3 bullets")
    return {"overview": got["Overview"], "why": why, "mental_model": got["Mental model"],
            "order": slugs, "order_reasons": reasons,
            "capstone": {"brief": capn["Brief"], "deliverables": dl, "rubric": rb}}, errs

# ---------------------------------------------------------------- drivers
def check_files(paths):
    bad = 0
    for p in paths:
        p = Path(p)
        if not p.exists(): print(f"✗ {p}: file not found"); bad += 1; continue
        if p.name == "_module.md":
            _, errs = parse_module(p)
        else:
            _, errs, _ = parse_topic(p)
        if errs:
            bad += 1
            print(f"✗ {p.relative_to(ROOT) if p.is_absolute() else p}")
            for e in errs: print(f"    - {e}")
        else:
            print(f"✓ {p.relative_to(ROOT) if p.is_absolute() else p}")
    return bad

def build(report_only=False):
    out_modules, report = [], {"ok": 0, "missing": [], "invalid": {}, "module_missing": [], "module_invalid": {}}
    for m in TAX["modules"]:
        mdir = ROOT / "content" / m["id"]
        intro, merrs = (None, [])
        if (mdir / "_module.md").exists():
            intro, merrs = parse_module(mdir / "_module.md")
            if merrs: report["module_invalid"][m["id"]] = merrs
        else:
            report["module_missing"].append(m["id"])
        topics = []
        for t in m["topics"]:
            p = mdir / f"{t['id']}.md"
            if not p.exists():
                report["missing"].append(t["key"])
                topics.append({**t, "module": m["id"], "status": "missing"})
                continue
            parsed, errs, _ = parse_topic(p)
            if parsed is None:
                report["invalid"][t["key"]] = errs
                topics.append({**t, "module": m["id"], "status": "invalid", "errors": errs})
                continue
            if errs:
                report["invalid"][t["key"]] = errs
                parsed["status"] = "invalid"; parsed["errors"] = errs
            else:
                parsed["status"] = "ok"; report["ok"] += 1
            topics.append(parsed)
        out_modules.append({**{k: v for k, v in m.items() if k != "topics"}, "intro": intro, "topics": topics})
    total = len(ALL_KEYS)
    print(f"topics ok={report['ok']}/{total} missing={len(report['missing'])} invalid={len(report['invalid'])} "
          f"| modules missing={len(report['module_missing'])} invalid={len(report['module_invalid'])}")
    (ROOT / "build_report.json").write_text(json.dumps(report, indent=1))
    if report_only:
        for k, v in list(report["invalid"].items())[:40]:
            print(f"  ✗ {k}: {v[0]}")
        return
    HEAVY = ("agent", "projects", "exercise", "mastery_test", "why", "how", "example", "patterns", "diagram", "interview", "tradeoffs", "pitfalls", "when", "role_lens", "check", "quiz", "sources")
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (ROOT / "site" / "data").mkdir(exist_ok=True)
    light_modules, total_bytes = [], 0
    cur = json.loads((ROOT / "curriculum.json").read_text()) if (ROOT / "curriculum.json").exists() else None
    track = {}
    if cur:
        n = 0
        for si, st in enumerate(cur["stages"]):
            for mid in st["modules"]:
                n += 1; track[mid] = {"stage": st["id"], "stage_index": si + 1, "track_order": n}
    for m in out_modules:
        m.update(track.get(m["id"], {}))
        light = [{k: v for k, v in t.items() if k not in HEAVY} for t in m["topics"]]
        light_modules.append({**m, "topics": light})
        full = [{k: v for k, v in t.items() if k in HEAVY or k == "key"} for t in m["topics"] if t.get("status") != "missing"]
        js = f'window.LMS_LOAD && window.LMS_LOAD({json.dumps(m["id"])}, ' + json.dumps({"topics": full}, ensure_ascii=False, separators=(",", ":")) + ');\n'
        (ROOT / "site" / "data" / f'{m["id"]}.js').write_text(js, encoding="utf-8"); total_bytes += len(js)
    data = {"meta": {"generated_from": TAX["generated_from"], "built_at": built, "build_id": built.replace(" ", "_").replace(":", ""),
                     "topic_count": total, "ok_count": report["ok"]},
            "tiers": TAX["tiers"], "role_paths": TAX["role_paths"],
            "stages": [{k: v for k, v in st.items() if k != "modules"} for st in cur["stages"]] if cur else [],
            "modules": light_modules}
    js = "window.LMS_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (ROOT / "site" / "data.js").write_text(js, encoding="utf-8")
    print(f"wrote site/data.js ({len(js)/1e6:.2f} MB index) + site/data/*.js ({total_bytes/1e6:.2f} MB lessons)")
    # site/page.html is the artifact-style page (no document skeleton); wrap it for local use.
    page = (ROOT / "site" / "page.html").read_text(encoding="utf-8")
    (ROOT / "site" / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
        + page + '\n</body>\n</html>\n', encoding="utf-8")
    print("wrote site/index.html")

if __name__ == "__main__":
    a = sys.argv[1:]
    if "--v2" in a:
        a.remove("--v2"); V2_FORCE = True
    if "--v3" in a:
        a.remove("--v3"); V3_FORCE = True; V2_FORCE = True
    if "--v4" in a:
        a.remove("--v4"); V4_FORCE = True; V3_FORCE = True; V2_FORCE = True
    if a[:1] == ["--check"]:
        sys.exit(1 if check_files(a[1:]) else 0)
    if a[:1] == ["--check-module"]:
        files = sorted((ROOT / "content" / a[1]).glob("*.md"))
        sys.exit(1 if check_files(files) else 0)
    build(report_only=(a[:1] == ["--report"]))
