"""FastAPI backend. Serves the web page and the bid API."""
from __future__ import annotations

import html
import json
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .config import AI_PROVIDER, DATA_DIR, DEMO_MODE, ENV_FILE, LIMITS, MODEL_NAME, RATES, REPO_DIR
from .models import BidRequest
from .pipeline import (BudgetExceeded, apply_costs, benchmark_bid, list_bids, load_bid, revise_bid, run_bid, save_bid,
                       what_if_bid)
from .safety import tokens_used_today
from . import walkthroughs

STATIC = REPO_DIR / "app" / "static"
ALLOWED_IMAGES = {"image/jpeg", "image/png", "image/webp"}

app = FastAPI(title="CFM Bid Builder")


from fastapi.responses import JSONResponse  # noqa: E402
from pydantic_ai.exceptions import ModelAPIError, ModelAuthenticationError  # noqa: E402


@app.exception_handler(ModelAuthenticationError)
async def _bad_key(_req, _exc):
    return JSONResponse(status_code=502, content={"detail": "The AI service rejected the API key (PORTKEY_API_KEY or OPENAI_API_KEY in .env). "
                                                  "Check the key, or leave it blank to use demo mode. Nothing was charged and no bid was saved."})


@app.exception_handler(ModelAPIError)
async def _ai_down(_req, exc):
    return JSONResponse(status_code=502, content={"detail": f"The AI service could not be reached or refused the request ({type(exc).__name__}). "
                                                  "No bid was saved; try again in a minute."})
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi import Response
    return Response(status_code=204)


@app.get("/api/status")
def status():
    return {
        "demo_mode": DEMO_MODE, "model": MODEL_NAME, "provider": AI_PROVIDER,
        "env_file": str(ENV_FILE.parent.name + "/.env") if ENV_FILE else None,
        "tokens_today": tokens_used_today(), "limits": LIMITS,
    }


@app.get("/api/sample")
def sample():
    return json.loads((DATA_DIR / "sample" / "sample_building.json").read_text(encoding="utf-8"))


@app.get("/api/rates")
def rates():
    return RATES


async def _read_image(f: UploadFile) -> tuple[str, bytes, str]:
    if f.content_type not in ALLOWED_IMAGES:
        raise HTTPException(400, f"{f.filename}: only JPG, PNG or WEBP photos are accepted.")
    data = await f.read()
    if len(data) > LIMITS["max_photo_mb"] * 1024 * 1024:
        raise HTTPException(400, f"{f.filename}: larger than {LIMITS['max_photo_mb']} MB.")
    return (f.filename or "photo", data, f.content_type)


@app.post("/api/bids")
async def create_bid(
    payload: str = Form(...),
    photos: list[UploadFile] = File(default=[]),
    floor_plan: UploadFile | None = File(default=None),
):
    try:
        req = BidRequest(**json.loads(payload))
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Bad bid form: {e}")
    if len(photos) > LIMITS["max_photos"]:
        raise HTTPException(400, f"Up to {LIMITS['max_photos']} photos per bid.")
    imgs = [await _read_image(p) for p in photos if p.filename]
    if req.walkthrough:
        try:
            imgs += walkthroughs.load_photos(req.walkthrough)
        except FileNotFoundError:
            raise HTTPException(404, "Saved walkthrough not found")
    if len(imgs) > LIMITS["max_photos"]:
        raise HTTPException(400, f"Up to {LIMITS['max_photos']} photos per bid (this one has {len(imgs)}).")
    plan = await _read_image(floor_plan) if floor_plan and floor_plan.filename else None
    if req.service_type in ("janitorial", "both") and not imgs and not req.areas and not plan:
        raise HTTPException(400, "For janitorial, add photos, a floor plan, or at least one area with square footage.")
    if req.service_type in ("floor_care", "both") and not req.floor_care:
        raise HTTPException(400, "Add at least one floor care line (area, service, square feet).")
    try:
        return await run_bid(req, imgs, plan)
    except BudgetExceeded as e:
        raise HTTPException(429, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/walkthroughs")
def list_walkthroughs():
    return walkthroughs.list_all()


@app.get("/api/walkthroughs/{slug}")
def get_walkthrough(slug: str):
    try:
        return walkthroughs.info(slug)
    except FileNotFoundError:
        raise HTTPException(404, "Saved walkthrough not found")


@app.get("/api/walkthroughs/{slug}/photos/{name}")
def walkthrough_photo(slug: str, name: str):
    try:
        return FileResponse(walkthroughs.photo_path(slug, name))
    except FileNotFoundError:
        raise HTTPException(404, "Photo not found")


@app.get("/api/bids")
def bids():
    return list_bids()


@app.get("/api/bids/{bid_id}")
def get_bid(bid_id: str):
    try:
        return load_bid(bid_id)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")


class RevisionIn(BaseModel):
    message: str = Field(..., min_length=3, max_length=1000)


@app.post("/api/bids/{bid_id}/revise")
async def revise(bid_id: str, r: RevisionIn):
    try:
        return await revise_bid(bid_id, r.message)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")
    except BudgetExceeded as e:
        raise HTTPException(429, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/bids/{bid_id}/benchmark")
async def benchmark(bid_id: str):
    try:
        return await benchmark_bid(bid_id)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")
    except BudgetExceeded as e:
        raise HTTPException(429, str(e))


class WhatIfIn(BaseModel):
    hourly_wage: float | None = Field(None, ge=7.25, le=60)
    visits_per_week: int | None = Field(None, ge=1, le=7)
    costs: dict[str, float] = Field(default_factory=dict)


@app.post("/api/bids/{bid_id}/whatif")
def whatif(bid_id: str, w: WhatIfIn):
    """Pure-code pricing curve and sensitivity for the What-if chart. No AI call, no cost, nothing saved."""
    try:
        return what_if_bid(bid_id, w.hourly_wage, w.visits_per_week, w.costs)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/bids/{bid_id}/costs")
def save_costs(bid_id: str, w: WhatIfIn):
    try:
        bid = load_bid(bid_id)
        return apply_costs(bid_id, w.hourly_wage or bid["pricing"]["hourly_wage"],
                           w.visits_per_week or bid["request"]["visits_per_week"], w.costs)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


class Decision(BaseModel):
    decision: str = Field(..., pattern="^(approve|send_back)$")
    approver: str = Field(..., min_length=2, max_length=80)
    note: str = Field("", max_length=1000)


@app.post("/api/bids/{bid_id}/decision")
def decide(bid_id: str, d: Decision):
    try:
        bid = load_bid(bid_id)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")
    stops = [f for f in bid["flags"] if f["level"] == "stop"]
    if d.decision == "approve" and stops and len(d.note.strip()) < 10:
        raise HTTPException(400, "This bid has a STOP flag. Add a note (10+ characters) explaining why it is OK to approve.")
    bid["status"] = "approved" if d.decision == "approve" else "sent_back"
    bid["decision"] = {**d.model_dump(), "time": datetime.now().isoformat(timespec="seconds")}
    bid["audit"].append({"time": bid["decision"]["time"], "step": "manager_decision", **d.model_dump()})
    save_bid(bid)
    return bid


@app.get("/api/bids/{bid_id}/proposal", response_class=HTMLResponse)
def proposal(bid_id: str):
    try:
        b = load_bid(bid_id)
    except FileNotFoundError:
        raise HTTPException(404, "Bid not found")
    e = html.escape
    p, pr, fc, rq, tot = b["proposal"], b.get("pricing"), b.get("floor_care"), b["request"], b["totals"]
    draft = b["status"] != "approved"
    letter = "".join(f"<p>{e(x)}</p>" for x in p["cover_letter"].split("\n") if x.strip())
    jan_scope = "".join(f"<li>{e(x)}</li>" for x in p["scope_of_work"] if not x.lower().startswith("floor care"))
    fc_scope = "".join(f"<li>{e(x[len('Floor care, '):])}</li>" for x in p["scope_of_work"] if x.lower().startswith("floor care, "))
    excl = "".join(f"<li>{e(x)}</li>" for x in p["exclusions"])
    sections = ""
    if pr:
        rows = "".join(f"<tr><td>{e(l['name'])}</td><td>{e(l['area_type'].replace('_',' '))}</td><td>{e(l['surface'])}</td>"
                       f"<td class=n>{l['sq_ft']:,}</td></tr>" for l in pr["lines"])
        sections += f"""<h2>Janitorial service ({rq['visits_per_week']} visits per week)</h2><ul>{jan_scope}</ul>
<table><tr><th>Area</th><th>Type</th><th>Floor</th><th class=n>Sq ft</th></tr>{rows}
<tr class=tot><td colspan=3>Total</td><td class=n>{pr['total_sq_ft']:,}</td></tr></table>"""
    if fc:
        rows = "".join(f"<tr><td>{e(l['name'])}</td><td>{e(l['service_label'])}</td><td class=n>{l['sq_ft']:,}</td>"
                       f"<td>{e(l['frequency_label'])}</td><td class=n>${l['price_per_sqft']:.2f}</td>"
                       f"<td class=n>${l['price_per_service']:,.2f}</td></tr>" for l in fc["lines"])
        sections += f"""<h2>Floor care</h2><ul>{fc_scope}</ul>
<table><tr><th>Area</th><th>Service</th><th class=n>Sq ft</th><th>How often</th><th class=n>Per sq ft</th><th class=n>Per service</th></tr>{rows}</table>"""
    price_rows = ""
    if pr:
        price_rows += (f"<tr><td>Janitorial, monthly</td><td class=n><b>${pr['monthly_price']:,.2f}</b></td></tr>"
                       f"<tr><td>Janitorial, annual</td><td class=n>${pr['annual_price']:,.2f}</td></tr>")
    if fc:
        if fc["one_time_total"]:
            price_rows += f"<tr><td>Floor care, one-time services</td><td class=n><b>${fc['one_time_total']:,.2f}</b></td></tr>"
        if fc["recurring_annual_total"]:
            price_rows += f"<tr><td>Floor care, recurring (per year)</td><td class=n>${fc['recurring_annual_total']:,.2f}</td></tr>"
    price_rows += f"<tr class=tot><td>First-year total</td><td class=n>${tot['first_year_value']:,.2f}</td></tr>"
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Proposal - {e(rq['client_name'])}</title>
<style>body{{font:15px/1.55 Georgia,serif;max-width:780px;margin:40px auto;color:#1d2733;padding:0 20px}}
h1{{font:600 26px system-ui;color:#0b3d5c;margin:0}}h2{{font:600 17px system-ui;color:#0b3d5c;margin-top:28px}}
table{{width:100%;border-collapse:collapse;font:14px system-ui}}td,th{{padding:6px 8px;border-bottom:1px solid #dde3ea;text-align:left}}
.n{{text-align:right}}.tot td{{font-weight:700;border-top:2px solid #0b3d5c}}.draft{{position:fixed;top:40%;left:15%;font:700 110px system-ui;color:#c0392b22;transform:rotate(-25deg)}}
.meta{{font:13px system-ui;color:#5b6b7a}}@media print{{button{{display:none}}}}</style></head><body>
{'<div class=draft>DRAFT</div>' if draft else ''}
<button onclick="print()" style="float:right">Print / Save PDF</button>
<h1>{'Janitorial and Floor Care' if pr and fc else 'Floor Care' if fc else 'Janitorial Services'} Proposal</h1>
<div class=meta>Corporate Facilities Management &middot; Prepared for {e(rq['client_name'])} &middot; {e(rq['building_name'])} &middot; {b['created'][:10]}</div>
{letter}
{sections}
<h2>Pricing summary</h2><table>{price_rows}</table>
<h2>Not included</h2><ul>{excl}</ul>
<p class=meta>{'Approved by ' + e(b['decision']['approver']) + ' on ' + b['decision']['time'][:10] if not draft else 'Draft: not yet approved by a manager.'}</p>
</body></html>"""
