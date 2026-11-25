#!/usr/bin/env python3
"""
Turns every weekly run in data/raw/ into site/data.json, which site/index.html draws.
Each run date is one week on the dashboard. Mock runs are skipped.

  python build_dashboard.py
"""
import collections, json, re
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
SITE = ROOT / "site"
ENGINES = ["chatgpt", "gemini", "perplexity"]
SKIP_GAP = {"road_trip"}  # answers are mostly generic foods, so a miss there isn't a real gap


def load():
    weeks = collections.defaultdict(list)
    for f in sorted(RAW.glob("*.jsonl")):
        if "mock" in f.name:
            continue
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                weeks[r["date"]].append(r)
    # The 25 Nov browser baseline used a different method (consumer apps by hand). If the API also
    # ran that day, keep the API answers so every week on the chart is measured the same way.
    for d, recs in weeks.items():
        api = [r for r in recs if "source" not in r]
        if api and len(api) < len(recs):
            weeks[d] = api
    return dict(sorted(weeks.items()))


def relevant(q, group):
    return q["category"] in (group, "both")


def week_stats(answers, questions, tracked):
    qmap = {q["id"]: q for q in questions}
    out = {}
    for brand, info in tracked.items():
        rel = [a for a in answers if relevant(qmap[a["question_id"]], info["group"])]
        named = [a for a in rel if brand in a["brands"]]
        by_engine = {}
        for e in ENGINES:
            re_ = [a for a in rel if a["engine"] == e]
            if re_:
                by_engine[e] = round(sum(brand in a["brands"] for a in re_) / len(re_), 3)
        by_q = {}
        for q in questions:
            if relevant(q, info["group"]):
                qa = [a for a in answers if a["question_id"] == q["id"]]
                by_q[q["id"]] = {"m": sum(brand in a["brands"] for a in qa), "n": len(qa)}
        out[brand] = {
            "mentions": len(named), "possible": len(rel),
            "share": round(len(named) / len(rel), 3) if rel else 0,
            "first": sum(a["brands"][:1] == [brand] for a in rel),
            "by_engine": by_engine, "by_q": by_q,
        }
    winners = {}
    for q in questions:
        c = collections.Counter()
        qa = [a for a in answers if a["question_id"] == q["id"]]
        for a in qa:
            c.update(set(a["brands"]))
        winners[q["id"]] = {"answers": len(qa), "top": sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:6]}  # stable order for ties
    return out, winners


def opener(brand, s, gaps, qmap, fit):
    pct = round(s["share"] * 100)
    if gaps:
        g = gaps[0]
        names = [w for w, _ in g["winners"][:3]]
        won = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else "other brands"
        q = qmap[g["qid"]]["text"].rstrip("?")
        if s["mentions"] == 0:
            return (f"I asked ChatGPT, Gemini and Perplexity {s['possible']} snack questions where {brand} fits this week, "
                    f"and none of them named it. For “{q},” they named {won} instead.")
        return (f"{brand} came up in {s['mentions']} of {s['possible']} AI answers this week ({pct}%), "
                f"but in none for “{q},” where {won} were named instead.")
    weak = [(v["m"] / v["n"], qid, v) for qid, v in s["by_q"].items()
            if qid in fit and v["n"] and 0 < v["m"] and v["m"] / v["n"] < 0.67]
    if weak:
        _, qid, v = min(weak)
        q = qmap[qid]["text"].rstrip("?")
        return (f"{brand} came up in {s['mentions']} of {s['possible']} AI answers this week, "
                f"but in only {v['m']} of {v['n']} for \u201c{q}.\u201d")
    eng = s["by_engine"]
    if len(eng) > 1 and max(eng.values()) - min(eng.values()) >= 0.2:
        hi, lo = max(eng, key=eng.get), min(eng, key=eng.get)
        label = {"chatgpt": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity"}
        return (f"{label[hi]} named {brand} in {round(eng[hi]*100)}% of relevant answers this week; "
                f"{label[lo]} named it in {round(eng[lo]*100)}%.")
    if s["first"] == 0 and s["mentions"]:
        return (f"{brand} came up in {s['mentions']} of {s['possible']} AI answers this week, "
                f"but wasn't the first brand named in any of them.")
    return f"{brand} came up in {s['mentions']} of {s['possible']} AI answers this week ({pct}%)."


# Brands with the same parent company aren't each other's competition in an email line
OWNER = {}
for parent, names in {
    "Hershey": ["Hershey's", "Reese's", "Kit Kat", "Lily's", "SkinnyPop", "LesserEvil", "Dot's", "Pirate's Booty", "Cadbury"],
    "PepsiCo": ["Doritos", "Cheetos", "Lay's", "Siete", "Tostitos", "Ruffles", "SunChips", "Fritos", "Popcorners", "Stacy's", "Smartfood"],
    "Mars": ["Snickers", "Twix", "3 Musketeers", "Milky Way", "M&M's", "Pringles", "Cheez-It"],
    "Campbell's": ["Kettle Brand", "Cape Cod", "Late July", "Snyder's", "Pop Secret"],
    "Mondelez": ["Hu", "Clif"],
}.items():
    for n in names:
        OWNER[n] = parent

CATEGORY = {"chips": "chip-buying", "candy": "candy and chocolate"}
ENGINE_LIST = "ChatGPT, Gemini and Perplexity"


def intro(brand, info, s, gaps, qmap, fit, v=0):
    """One plain first line for an email: where the brand stands in AI answers, then the clearest miss.
    v picks the wording, so a list of these doesn't open the same way every time."""
    cat = CATEGORY.get(info["group"], "snack")
    about = "chips" if info["group"] == "chips" else "candy and chocolate"
    b, m, n, pct = brand, s["mentions"], s["possible"], round(s["share"] * 100)
    if s["share"] < 0.10:
        leads = [
            f"Right now {b} is barely showing up in AI search. This week {ENGINE_LIST} named {b} in only {m} of {n} answers to {cat} questions ({pct}%).",
            f"{b} was named in just {m} of {n} AI answers to {cat} questions this week ({pct}%), across {ENGINE_LIST}.",
            f"When shoppers ask ChatGPT, Gemini or Perplexity about {about}, {b} almost never comes up: {m} of {n} answers this week ({pct}%).",
            f"Out of {n} answers {ENGINE_LIST} gave to {cat} questions this week, {b} appeared in {m} ({pct}%).",
        ]
    elif s["share"] < 0.25:
        leads = [
            f"Right now {b} is missing from most AI answers in its category. This week {ENGINE_LIST} named {b} in only {m} of {n} answers to {cat} questions ({pct}%).",
            f"{b} is left out of most AI shopping answers: {ENGINE_LIST} named it in {m} of {n} {cat} answers this week ({pct}%).",
            f"Out of {n} AI answers to {cat} questions this week, {b} came up in {m} ({pct}%).",
            f"Most times an AI assistant answers a question about {about}, {b} isn't in the answer. This week it was named in {m} of {n} ({pct}%).",
        ]
    else:
        first = (", but was never the first brand recommended." if s["first"] == 0 else
                 f", and was the first brand recommended {s['first']} times.")
        leads = [
            f"{b} shows up in {m} of {n} AI answers to {cat} questions ({pct}%) across {ENGINE_LIST} this week" + first,
            f"{b} is one of the more visible brands in AI answers about {about}, named in {m} of {n} this week ({pct}%)" + first,
            f"{ENGINE_LIST} named {b} in {m} of {n} {cat} answers this week ({pct}%)."
            + (" It was never the first brand recommended." if s["first"] == 0 else
               f" It was the first brand recommended {s['first']} times."),
        ]
    lead = leads[v % len(leads)]
    t = (v + 1)  # offset so the two halves of a line don't rotate together
    if gaps:
        g = gaps[0]
        w = [x[0] for x in g["winners"] if OWNER.get(x[0]) is None or OWNER.get(x[0]) != OWNER.get(brand)][:3]
        q = qmap[g["qid"]]["text"].rstrip("?")
        names = f"{', '.join(w[:-1])} and {w[-1]}" if len(w) > 1 else (w[0] if w else "other brands")
        tails = [
            f" When asked \u201c{q}?\u201d none of them mentioned {b}; they recommended {names} instead.",
            f" Ask \u201c{q}?\u201d and {b} doesn't come up at all; the answers go to {names}.",
            f" For \u201c{q}?\u201d the engines recommended {names}, and never {b}.",
        ]
        return lead + tails[t % len(tails)]
    weak = [(x["m"] / x["n"], qid, x) for qid, x in s["by_q"].items()
            if qid in fit and x["n"] and x["m"] / x["n"] < 0.5]
    if weak:
        _, qid, x = min(weak)
        q = qmap[qid]["text"].rstrip("?")
        tails = [f" On \u201c{q}?\u201d it came up in just {x['m']} of {x['n']} answers.",
                 f" It appeared in only {x['m']} of {x['n']} answers to \u201c{q}?\u201d"]
        return lead + tails[t % len(tails)]
    eng = s["by_engine"]
    if len(eng) > 1 and max(eng.values()) - min(eng.values()) >= 0.2:
        label = {"chatgpt": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity"}
        hi, lo = max(eng, key=eng.get), min(eng, key=eng.get)
        return lead + (f" It depends heavily on the engine: {label[hi]} named it in {round(eng[hi]*100)}% "
                       f"of those answers, {label[lo]} in only {round(eng[lo]*100)}%.")
    return lead


def main():
    questions = json.loads((ROOT / "config" / "questions.json").read_text())
    brands = json.loads((ROOT / "config" / "brands.json").read_text())
    leads = json.loads((ROOT / "config" / "leads.json").read_text())
    tracked = brands["tracked"]
    qmap = {q["id"]: q for q in questions}

    weeks = load()
    if not weeks:
        raise SystemExit("No runs in data/raw yet.")
    dates = list(weeks)
    per_week = {d: week_stats(a, questions, tracked) for d, a in weeks.items()}
    latest, prev = dates[-1], (dates[-2] if len(dates) > 1 else None)
    stats, winners = per_week[latest]

    out_brands = []
    for brand, info in tracked.items():
        s = stats[brand]
        gaps = []
        fit = info.get("fit", list(s["by_q"]))
        for qid in fit:
            v = s["by_q"].get(qid, {"m": 0, "n": 0})
            if qid in SKIP_GAP or v["m"] > 0 or v["n"] == 0:
                continue
            gaps.append({"qid": qid, "winners": [w for w in winners[qid]["top"] if w[0] != brand]})
        # keep fit order: the first fit question missed is the most telling gap
        change = None
        if prev:
            change = round(s["share"] - per_week[prev][0][brand]["share"], 3)
        # opportunity: how much of its own category the brand is missing, plus never being first
        opp = round((1 - s["share"]) * 70 + len(gaps) * 5 + (10 if s["first"] == 0 else 0))
        lead = {k: v for k, v in leads.get(brand, {}).items() if not k.startswith("_")}
        out_brands.append({
            "brand": brand, "group": info["group"], **s, "change": change,
            "gaps": [{"question": qmap[g["qid"]]["text"], "winners": g["winners"][:4]} for g in gaps],
            "opportunity": opp, "lead": lead, "opener": opener(brand, s, gaps, qmap, fit),
            "_intro_args": (brand, info, s, gaps, qmap, fit),
            "series": [{"date": d, "share": per_week[d][0][brand]["share"],
                        "by_engine": per_week[d][0][brand]["by_engine"]} for d in dates],
        })
    out_brands.sort(key=lambda b: -b["opportunity"])
    for i, ob in enumerate(out_brands):  # rank order, so neighbouring rows get different wording
        ob["intro"] = intro(*ob.pop("_intro_args"), v=i)

    # Outreach tab: every contact, with the intro for the weakest brand they cover
    by_name = {b["brand"]: b for b in out_brands}
    people = json.loads((ROOT / "config" / "contacts.json").read_text())["contacts"]
    outreach = []
    for p in people:
        covered = [by_name[b] for b in p["brands"] if b in by_name]
        if not covered:
            continue
        focus = max(covered, key=lambda b: b["opportunity"])
        outreach.append({**p, "focus": focus["brand"], "share": focus["share"],
                         "mentions": focus["mentions"], "possible": focus["possible"],
                         "opportunity": focus["opportunity"], "intro": focus["intro"]})
    outreach.sort(key=lambda p: -p["opportunity"])

    meta_sources = sorted({a.get("source", "API run") for a in weeks[latest]})
    data = {
        "updated": latest, "previous": prev, "weeks": dates,
        "answers_this_week": len(weeks[latest]),
        "engines": sorted({a["engine"] for a in weeks[latest]}),
        "source_this_week": ", ".join(meta_sources),
        "questions": [{"id": q["id"], "text": q["text"], "category": q["category"],
                       "answers": winners[q["id"]]["answers"], "top": winners[q["id"]]["top"]} for q in questions],
        "brands": out_brands,
        "outreach": outreach,
    }
    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, indent=1))
    print(f"site/data.json: {len(dates)} week(s), latest {latest}, {len(weeks[latest])} answers")
    for b in out_brands[:5]:
        print(f"  #{b['opportunity']:3d} {b['brand']}: {b['opener']}")


if __name__ == "__main__":
    main()
