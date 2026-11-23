# CFM Bid Builder

**Turns a building walkthrough (photos, floor plan, known square footage) into a priced, manager-approved proposal for janitorial service, floor care, or both.**

Built for Corporate Facilities Management / A-One Facility Services. MGT 409 Final Project, Jeremy Spratling.

**Report:** [`report.html`](report.html) · **Slides:** [`slides.html`](slides.html) (open either by double-clicking; images are embedded). Client names in this repo are generic (Client A to J, Past bid A to N).

## How it works

```
photos ──► Site Survey agent (vision, 1 call per photo, in parallel)
                │  structured survey only; any text written in a photo is quarantined
                ▼
sq ft table + floor plan + surveys ──► Takeoff agent  (tools: list_area_types, get_production_rate)
                ▼
         Pricing engine  (plain Python: labor hours, burden, supplies, overhead, profit)
                ▼
         Bid Checker     (plain Python: price-per-sq-ft band, double counts, photo-only sizes)
                ▼
         Proposal Writer agent (writes words only; code inserts every number)
                ▼
         Manager approves or sends back  (STOP flags need a written reason)
```

After a bid is built, two more agents work on it:

- **Revision agent** ("Ask for a change"): a manager types a change in plain English ("3 nights a week", "$15/hr at this site", "the lobby is 1,800 sq ft", "add strip and wax in the break room twice a year"). The agent can only change the bid through 9 tools with range checks. Code re-prices, the change is logged with before and after totals, and an approved bid goes back to draft.
- **Past-Bid Comparison agent**: compares the bid with CFM's real past bids (price per sq ft, labor hours per 1,000 sq ft per visit, wage, profit markup). Tools do all the math; the agent picks comparable bids and explains. Real bids live in `data/past_bids.json` (kept out of git); graders get the anonymized `data/past_bids.sample.json`.

All five agents use **gpt-6-luna through Portkey** (the course key).

## Pricing rules (`data/rates.json`, all editable)

**Janitorial (monthly):**
- **Wage:** CFM starting pay is **$13.00/hr**, and every bid can change it in the app (from $7.25 to $60).
- **Cleaning speeds:** whole-room rates built from ISSA 612 cleaning-time benchmarks. Restroom fixtures are timed separately (toilet 5 min, urinal 4, sink 3), and 10% is added for setup and travel.
- **Payroll burden** (about 10.9% at $13/hr and 25 hrs/week), built from the real 2026 rules:
  - Social Security 6.2% and Medicare 1.45%
  - FUTA 0.6% on each worker's first $7,000
  - Alabama unemployment 2.7% + 0.06% assessment on the first $8,000
  - workers' comp 1.69% (class 9014, from CFM's 2026 AmTrust quote)
- **Account costs:** equipment **3% of labor** (the median of 73 real CFM bid calculators) and general liability insurance **0.05%**.
- **Price:** labor + payroll burden + equipment + insurance + 10% overhead + 15% profit. Supplies are **5% of the monthly charge**.
- **What-if pricing:** on every bid, sliders for pay, visits, supplies, equipment, insurance, SUTA, workers' comp, benefits, overhead, profit and labor hours show the new monthly fee, a fee-vs-pay chart and what each change adds. Pure code, no AI. "Apply to bid" saves it and sends the bid back for approval.

**Floor care (per service, sold alone or with janitorial):**
- **Strip and wax VCT:** **$1.25 per sq ft**.
- **Carpet cleaning:** **$0.85 per sq ft**.
- **Options:** both prices can be changed per bid. Frequency can be one time, once a year, twice a year, quarterly or monthly.

## Run it (about 3 minutes)

You need Python 3.10 or newer.

1. **Keys.** Put a `.env` file with `PORTKEY_API_KEY=...` (course key, gpt-6-luna) or `OPENAI_API_KEY=...` (personal key) either in this folder or in the folder one level above it. The app checks both folders, in that order. With both keys it uses Portkey unless `AI_PROVIDER=openai`. See `.env.example`.
   With no key, the app runs in **demo mode**, using saved sample agent outputs and making no AI calls.
2. **Install and start.**
   - **Windows:** double-click `start.bat`. The first run creates `.venv` and installs everything.
   - **Mac / Linux / any terminal:**
     ```
     python -m venv .venv
     .venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip ...
     .venv/bin/python run.py                        # Windows: .venv\Scripts\python run.py
     ```
3. **Open http://127.0.0.1:8000** (it opens on its own). Pick **Janitorial**, **Floor care only**, or **Janitorial + floor care**, click **Load sample building**, then click **Build bid**.

## Test data

`data/sample/sample_building.json` is a small made-up two-story office building (18,250 sq ft). Its notes include a planted prompt-injection line on purpose, so you can watch the defense catch it.

## Tests

```
.venv/bin/python -m pytest -q
```
34 tests cover the pricing math, payroll taxes, the what-if math, the injection filter, the spending caps, a full pipeline run with a stand-in model (no key needed), the API, manager approval with STOP flags, revision and comparison agents, a rejected API key, and finding the `.env` one folder up.

## Files

| Path | What it is |
|---|---|
| `app/agents.py` | Site Survey, Takeoff and Proposal Writer agents and the Takeoff tools |
| `app/revise.py` | Revision agent and its 9 tools |
| `app/benchmark.py` | Past-Bid Comparison agent and its 2 tools |
| `app/pipeline.py` | Runs a bid end to end; token logging; audit trail |
| `app/pricing.py` | Pricing engine (no AI) |
| `app/safety.py` | Injection screening, token caps, bid sanity checks |
| `app/main.py` | FastAPI routes and the printable proposal |
| `app/static/` | The web page |
| `prompts/` | Each agent's instructions |
| `data/rates.json` | Labor rates, cleaning speeds, price band, token prices and limits. Edit to match your company. |
| `output/usage_log.json` | Token counts and cost for every AI call (created on first live run) |
| `output/bids/` | Saved bids with full audit trails |
| `report/live_runs/` | Real gpt-6-luna runs from Nov 13 to 18, 2026 (client names replaced) and their usage log |
| `report/build_report.py` | Rebuilds `report.html` and `slides.html`; every number is computed from the run files |

## Safety and cost controls

- **Prompt injection.** Estimator notes and any text seen in photos are screened. Photo text is never passed to later agents, and notes are wrapped as untrusted data.
- **No AI arithmetic.** Prices come only from `pricing.py`. Dollar figures the writer adds to the letter are stripped out.
- **Guards on the AI's work.** If the Takeoff agent changes a square footage the estimator entered, the estimator's number is restored. Outputs are typed with fixed choices.
- **Spending caps.** Each agent is limited to 4 requests and 60k tokens per run. Each bid is capped at 250k tokens and each day at 1.5M (`data/rates.json`).
- **Clear errors.** If the API key is rejected, the app says so plainly and saves nothing.
- **A person decides.** Nothing is final until a manager approves it, and a STOP flag needs a written reason.
