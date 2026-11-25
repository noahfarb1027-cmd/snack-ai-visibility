# AI Visibility Tracker: snack brands

Every Monday this asks ChatGPT (OpenAI), Gemini and Perplexity 12 snack-buying questions, counts which of 20 tracked brands each answer names, and appends a scorecard to `data/results.csv`. It's an automated version of the manual audit from November 25, 2026, which is saved as the baseline in `data/baseline-2026-11-25-browser.csv`.

## What's in here

| File | What it does |
| --- | --- |
| `tracker.py` | Asks the questions, matches brands, writes raw answers and the scorecard |
| `config/questions.json` | The 12 questions and whether each is a chips, candy or both question |
| `config/brands.json` | The 20 tracked brands and ~50 competitors, as regex patterns |
| `.github/workflows/weekly.yml` | Runs it every Monday on GitHub Actions and commits the results |
| `data/raw/YYYY-MM-DD.jsonl` | Every answer, word for word, so any number can be checked |
| `data/results.csv` | One row per engine × brand × week: mentions, possible answers, share, times named first |

## Setup (about 20 minutes)

1. **Get three API keys.**
   - OpenAI: platform.openai.com → API keys. Add about $5 of credit.
   - Gemini: aistudio.google.com → Get API key.
   - Perplexity: perplexity.ai/settings/api. Add about $5 of credit.
2. **Test it locally without keys:** `pip install -r requirements.txt` then `python tracker.py --mock`.
3. **Run it once for real:**
   ```
   export OPENAI_API_KEY=...  GEMINI_API_KEY=...  PERPLEXITY_API_KEY=...
   python tracker.py --runs 3
   ```
   An engine with no key is skipped, so you can start with one.
4. **Put it on GitHub.** Create a private repo, push this folder, then go to Settings → Secrets and variables → Actions and add `OPENAI_API_KEY`, `GEMINI_API_KEY` and `PERPLEXITY_API_KEY`. Open the Actions tab and click "Run workflow" once to check it works. After that it runs every Monday on its own.
5. **Optional: Google Sheet.** Create a Google Cloud service account, share a Sheet with its email, and add the service-account JSON as `GOOGLE_SERVICE_ACCOUNT_JSON` and the Sheet's ID as `SHEET_ID`. Each run then appends to the Sheet too, which a chart can read live.

## The dashboard (GitHub Pages)

`build_dashboard.py` turns every week in `data/raw/` into `site/data.json`, and `site/index.html` draws it:
the share scale for all 20 brands, a trend line per brand (overall and per engine), where each brand is missing,
and a lead queue ranked by opportunity with an auto-written opening line for each brand.

To turn it on: repo **Settings → Pages → Source: GitHub Actions**. The weekly job then publishes the page at
`https://<your-username>.github.io/<repo-name>/` after every run. To preview locally:
`python build_dashboard.py && python -m http.server -d site` and open http://localhost:8000.

The page is public, so it shows only names, titles and LinkedIn links (`config/leads.json`).
Email addresses stay in your private contacts CSV and are never committed here. Edit `config/leads.json`
to change who's listed, and each brand's `fit` list in `config/brands.json` to change which questions its
opening line draws on.

## Cost

12 questions × 3 engines × 3 runs = 108 calls a week. With web search on, that's roughly $1–3 a week across the three providers at current small-model prices. Check each provider's pricing page before you rely on that number.

## Settings

- `--runs N`: how many times to ask each question per engine (default 2). More runs smooth out the answer-to-answer variation.
- `OPENAI_MODEL`, `GEMINI_MODEL`, `PERPLEXITY_MODEL`: which model each engine uses (defaults: `gpt-4.1-mini`, `gemini-2.5-flash`, `sonar`). Set these to whatever is current when you run it.
- Add questions or brands by editing the two files in `config/`. A brand's pattern is a regex; test new ones before relying on them.

## Known limits

- **API answers aren't the consumer apps.** The apps add personalization, location and their own formatting. Web search is switched on for OpenAI and Gemini to get closer, and Perplexity's Sonar searches by default.
- **A mention isn't always a recommendation.** "Unlike a Snickers..." still counts. In the manual baseline those were removed by hand. Read the raw answers before quoting a surprising number.
- **"Named first"** is the first brand in the answer that matches any pattern in `config/brands.json`.
