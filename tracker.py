#!/usr/bin/env python3
"""
AI Visibility Tracker: asks ChatGPT (OpenAI), Gemini and Perplexity the same
buyer questions, counts which brands each answer names, and appends a weekly
scorecard to data/results.csv.

Usage:
  python tracker.py                 # real run (needs API keys in env)
  python tracker.py --mock          # fake answers, no keys, for testing
  python tracker.py --runs 3        # ask each question N times per engine
  python tracker.py --engines chatgpt,perplexity

Env vars:
  OPENAI_API_KEY, GEMINI_API_KEY, PERPLEXITY_API_KEY   (an engine without a key is skipped)
  OPENAI_MODEL      default gpt-4.1-mini
  GEMINI_MODEL      default gemini-2.5-flash
  PERPLEXITY_MODEL  default sonar
  GOOGLE_SERVICE_ACCOUNT_JSON + SHEET_ID   (optional: also append results to a Google Sheet)
"""
import argparse, csv, datetime as dt, json, os, random, re, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
TIMEOUT = 120


# ---------------------------------------------------------------- engines
def ask_openai(q):
    key = os.environ["OPENAI_API_KEY"].strip()  # pasted keys often carry a stray space or newline
    model = os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
    # web search on, so the answer reflects what ChatGPT with search would say
    for tool in ("web_search", "web_search_preview"):
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "input": q, "tools": [{"type": tool}], "tool_choice": "required"},  # search every time, like the ChatGPT app does for shopping questions
            timeout=TIMEOUT,
        )
        if r.status_code == 400 and tool == "web_search":
            continue  # older accounts only have the preview tool name
        r.raise_for_status()
        out = []
        for item in r.json().get("output", []):
            if item.get("type") == "message":
                out += [c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text"]
        return "\n".join(out)
    raise RuntimeError("OpenAI web search tool unavailable")


def ask_gemini(q):
    key = os.environ["GEMINI_API_KEY"].strip()  # pasted keys often carry a stray space or newline
    model = os.getenv("GEMINI_MODEL") or "gemini-flash-latest"  # alias follows Google's current Flash model
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": q}]}], "tools": [{"google_search": {}}]},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    cands = r.json().get("candidates", [])
    parts = cands[0]["content"].get("parts", []) if cands else []
    return "\n".join(p.get("text", "") for p in parts)


def ask_perplexity(q):
    key = os.environ["PERPLEXITY_API_KEY"].strip()  # pasted keys often carry a stray space or newline
    effort = os.getenv("PERPLEXITY_EFFORT") or "fast"  # "fast" is what the old Sonar model maps to
    headers = {"Authorization": f"Bearer {key}"}
    body = {"input": q, "effort": effort}
    # Perplexity replaced Sonar's chat/completions with its Agent API; try both paths it has documented
    for url in ("https://api.perplexity.ai/v1/agent", "https://api.perplexity.ai/v1/responses"):
        r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        if r.status_code == 404 and url.endswith("/agent"):
            continue
        r.raise_for_status()
        data = r.json()
        if data.get("output_text"):
            return data["output_text"]
        out = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                out += [c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text"]
        return "\n".join(out)
    raise RuntimeError("Perplexity Agent API unavailable")


def ask_mock(q, engine, brands):
    """Fake answer naming a random handful of brands, for testing without keys."""
    names = list(brands["tracked"]) + list(brands["competitors"])
    rng = random.Random(f"{engine}|{q}|{time.time_ns()}")
    picks = rng.sample(names, k=rng.randint(3, 8))
    return "Here are some good options:\n" + "\n".join(f"{i+1}. {p}" for i, p in enumerate(picks))


ENGINES = {
    "chatgpt": ("OPENAI_API_KEY", ask_openai),
    "gemini": ("GEMINI_API_KEY", ask_gemini),
    "perplexity": ("PERPLEXITY_API_KEY", ask_perplexity),
}


# ---------------------------------------------------------------- scoring
def compile_patterns(brands):
    pats = {}
    for name, b in brands["tracked"].items():
        pats[name] = re.compile(b["pattern"], re.I)
    for name, p in brands["competitors"].items():
        pats[name] = re.compile(p, re.I)
    return pats


def mentions(text, pats):
    """Brands named in the text, in order of first appearance."""
    hits = []
    for name, pat in pats.items():
        m = pat.search(text)
        if m:
            hits.append((m.start(), name))
    return [name for _, name in sorted(hits)]


def relevant(question, group):
    return question["category"] in (group, "both")


def score(answers, questions, brands):
    """One row per (engine, brand): mentions / relevant answers, times named first."""
    qmap = {q["id"]: q for q in questions}
    rows = []
    engines = sorted({a["engine"] for a in answers})
    for engine in engines + ["all"]:
        subset = [a for a in answers if engine == "all" or a["engine"] == engine]
        for brand, info in brands["tracked"].items():
            rel = [a for a in subset if relevant(qmap[a["question_id"]], info["group"])]
            named = [a for a in rel if brand in a["brands"]]
            first = [a for a in rel if a["brands"][:1] == [brand]]
            rows.append({
                "engine": engine, "brand": brand, "group": info["group"],
                "mentions": len(named), "possible": len(rel),
                "share": round(len(named) / len(rel), 3) if rel else 0,
                "first": len(first),
            })
    return rows


# ---------------------------------------------------------------- output
def append_csv(path, rows, fields):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def push_to_sheet(rows, fields):
    creds, sheet_id = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), os.getenv("SHEET_ID")
    if not (creds and sheet_id):
        return
    import gspread  # only needed when a Sheet is configured
    gc = gspread.service_account_from_dict(json.loads(creds))
    ws = gc.open_by_key(sheet_id).sheet1
    if not ws.get_all_values():
        ws.append_row(fields)
    ws.append_rows([[r[f] for f in fields] for r in rows])
    print(f"pushed {len(rows)} rows to Google Sheet")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2, help="times to ask each question per engine")
    ap.add_argument("--engines", default="chatgpt,gemini,perplexity")
    ap.add_argument("--mock", action="store_true", help="fake answers, no API calls")
    args = ap.parse_args()

    questions = json.loads((CONFIG / "questions.json").read_text())
    brands = json.loads((CONFIG / "brands.json").read_text())
    pats = compile_patterns(brands)
    today = dt.date.today().isoformat()
    DATA.mkdir(exist_ok=True)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    active = []
    for e in engines:
        key_var, _ = ENGINES[e]
        if args.mock or os.getenv(key_var):
            active.append(e)
        else:
            print(f"skipping {e}: {key_var} not set", file=sys.stderr)
    if not active:
        sys.exit("No engines to run. Set API keys or use --mock.")

    answers, failures = [], 0
    raw_path = DATA / "raw" / f"{today}{'-mock' if args.mock else ''}.jsonl"
    raw_path.parent.mkdir(exist_ok=True)
    with raw_path.open("w") as raw:
        for e in active:
            for q in questions:
                for run in range(1, args.runs + 1):
                    try:
                        text = ask_mock(q["text"], e, brands) if args.mock else ENGINES[e][1](q["text"])
                    except Exception as err:  # keep going; one bad call shouldn't kill the week
                        failures += 1
                        detail = ""
                        resp = getattr(err, "response", None)
                        if resp is not None:
                            detail = " | " + resp.text[:200].replace("\n", " ")
                        print(f"[{e}] {q['id']} run {run} failed: {str(err).split('?')[0]}{detail}", file=sys.stderr)
                        continue
                    found = mentions(text, pats)
                    rec = {"date": today, "engine": e, "question_id": q["id"], "run": run,
                           "brands": found, "text": text}
                    raw.write(json.dumps(rec) + "\n")
                    answers.append(rec)
                    shown = ', '.join(found[:6]) or f"(no brands; {len(text)} chars: {text[:100]!r})"
                    print(f"[{e}] {q['id']} run {run}: {shown}")
                    if not args.mock:
                        time.sleep(1)  # stay well under rate limits

    if not answers:
        sys.exit("Every call failed; nothing to score.")

    fields = ["date", "engine", "brand", "group", "mentions", "possible", "share", "first"]
    rows = [{"date": today, **r} for r in score(answers, questions, brands)]
    out = DATA / ("results-mock.csv" if args.mock else "results.csv")
    append_csv(out, rows, fields)
    if not args.mock:
        push_to_sheet(rows, fields)

    print(f"\n{len(answers)} answers, {failures} failures. Scorecard appended to {out}")
    top = sorted((r for r in rows if r["engine"] == "all"), key=lambda r: -r["share"])
    for r in top:
        print(f"  {r['brand']:20s} {r['mentions']:3d}/{r['possible']:<3d} {r['share']*100:5.1f}%  first {r['first']}")
    if failures and failures >= len(answers):
        sys.exit(1)


if __name__ == "__main__":
    main()
