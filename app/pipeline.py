"""Runs one bid end to end:

  photos ─► Site Survey agent (one call per photo, in parallel)
                     │ structured surveys only (photo text is quarantined)
                     ▼
  sq ft table + floor plan + surveys ─► Takeoff agent (tools: list_area_types, get_production_rate)
                     │ area list
                     ▼
              Pricing engine (plain Python, no AI)
                     ▼
              Bid Checker (plain Python sanity rules)
                     ▼
              Proposal Writer agent (writes words, never numbers)
                     ▼
              Manager approves or sends back
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import date, datetime

from pydantic_ai import BinaryContent
from pydantic_ai.usage import UsageLimits

from . import agents
from .config import BIDS_DIR, DATA_DIR, DEMO_MODE, LIMITS, MODEL_NAME, TOKEN_PRICES
from .images import shrink_photo
from .models import Area, BidRequest, PhotoSurvey, Proposal, Takeoff
from .pricing import default_wage, price_bid, price_floor_care
from .safety import (BudgetExceeded, append_usage, check_bid, check_bid_budget,
                     check_daily_budget, scan_for_injection, wrap_untrusted)


def model_for(model, name: str):
    """Tests can pass one model, or a dict of {agent name: model}."""
    return model.get(name) if isinstance(model, dict) else model


def _limits() -> UsageLimits:
    return UsageLimits(request_limit=LIMITS["max_requests_per_agent"],
                       total_tokens_limit=LIMITS["max_tokens_per_agent"])


def usage_record(agent_name: str, bid_id: str, usage, seconds: float) -> dict:
    inp = usage.input_tokens or 0
    cached = usage.cache_read_tokens or 0
    out = usage.output_tokens or 0
    uncached = max(inp - cached, 0)
    cost = (uncached * TOKEN_PRICES["input"] + cached * TOKEN_PRICES["cached_input"]
            + out * TOKEN_PRICES["output"]) / 1_000_000
    return {
        "date": date.today().isoformat(), "time": datetime.now().isoformat(timespec="seconds"),
        "bid_id": bid_id, "agent": agent_name, "model": MODEL_NAME,
        "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
        "total_tokens": inp + out, "requests": usage.requests, "tool_calls": usage.tool_calls,
        "cost_usd": round(cost, 6), "seconds": round(seconds, 2),
    }


class Run:
    def __init__(self, bid_id: str):
        self.bid_id = bid_id
        self.usage: list[dict] = []
        self.audit: list[dict] = []

    def log(self, step: str, detail: dict):
        self.audit.append({"time": datetime.now().isoformat(timespec="seconds"), "step": step, **detail})

    @property
    def tokens(self) -> int:
        return sum(u["total_tokens"] for u in self.usage)

    async def call(self, agent, name: str, prompt):
        check_daily_budget()
        check_bid_budget(self.tokens)
        t0 = time.perf_counter()
        result = await agent.run(prompt, usage_limits=_limits())
        rec = usage_record(name, self.bid_id, result.usage, time.perf_counter() - t0)
        self.usage.append(rec)
        return result.output


async def survey_photo(run: Run, model, name: str, data: bytes, mime: str, gate: asyncio.Semaphore) -> PhotoSurvey:
    small, info = shrink_photo(data)
    run.log("photo_resize", {"photo": name, **info})
    agent = agents.build_survey_agent(model_for(model, "site_survey"))
    async with gate:
        out: PhotoSurvey = await run.call(agent, "site_survey", [
            f"Survey this walkthrough photo. File name: {name}",
            BinaryContent(data=small, media_type="image/jpeg" if "error" not in info else mime),
        ])
    out.photo_name = name
    return out


FLOOR_EXCLUSION_WORDS = {"strip_wax": ("strip", "wax", "vct"), "carpet_clean": ("carpet clean", "carpet extraction", "extraction", "shampoo")}


def floor_care_scope(fc: dict | None) -> list[str]:
    """Scope lines for floor care are written by code so they always match what was priced."""
    if not fc:
        return []
    out = []
    for l in fc["lines"]:
        if l["service"] == "strip_wax":
            out.append(f"Floor care, {l['name']}: Strip old finish, deep scrub, and apply new sealer and wax coats to VCT "
                       f"({l['sq_ft']:,} sq ft, {l['frequency_label'].lower()})")
        else:
            out.append(f"Floor care, {l['name']}: Hot-water extraction carpet cleaning with pre-treatment of spots "
                       f"({l['sq_ft']:,} sq ft, {l['frequency_label'].lower()})")
    return out


def demo_floor_letter(req: BidRequest) -> str:
    return (f"Thank you for inviting Corporate Facilities Management to look at the floors at {req.building_name}. "
            "Clean, well-kept floors are the first thing your visitors notice and they protect one of your building's "
            "most expensive surfaces.\n\nThe services below were measured area by area so you can see exactly what is "
            "included and how often we will do it. Work is scheduled after hours or on weekends so it does not disrupt "
            "your staff.\n\nPlease review the scope and the pricing summary below. We are glad to walk the floors with "
            "you before you decide.")


async def run_bid(req: BidRequest, photos: list[tuple[str, bytes, str]],
                  floor_plan: tuple[str, bytes, str] | None = None, model=None) -> dict:
    bid_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    run = Run(bid_id)
    demo = DEMO_MODE and model is None
    alerts: list[dict] = []
    janitorial = req.service_type in ("janitorial", "both")
    floor = req.service_type in ("floor_care", "both")

    # 0. Screen the estimator's notes before any AI sees them.
    hits = scan_for_injection(req.estimator_notes)
    if hits:
        alerts.append({"source": "estimator notes", "phrases": hits, "action": "Kept as quoted data only, never followed"})
    run.log("intake", {"client": req.client_name, "building": req.building_name, "service_type": req.service_type,
                       "hourly_wage": req.hourly_wage or default_wage(), "photos": len(photos),
                       "floor_plan": bool(floor_plan), "known_areas": len(req.areas),
                       "floor_care_items": len(req.floor_care), "injection_hits": hits})
    if len(photos) > LIMITS["max_photos"]:
        raise ValueError(f"Too many photos: {len(photos)} (max {LIMITS['max_photos']}).")

    sample = json.loads((DATA_DIR / "sample" / "demo_run.json").read_text(encoding="utf-8")) if demo else None
    if demo:
        run.log("demo_mode", {"note": "No API key or DEMO_MODE=1: using saved sample agent outputs. No AI calls."})

    # 1. Site Survey: one call per photo, in parallel.
    if demo:
        surveys = [PhotoSurvey(**s) for s in sample["surveys"]] if janitorial else []
    else:
        gate = asyncio.Semaphore(LIMITS["parallel_photo_calls"])
        surveys = list(await asyncio.gather(*[survey_photo(run, model, n, d, m, gate) for n, d, m in photos]))

    # Screen any text the survey agent copied out of the photos.
    for s in surveys:
        photo_hits = scan_for_injection(s.text_seen_in_photo)
        if photo_hits:
            alerts.append({"source": f"text in photo {s.photo_name}", "phrases": photo_hits,
                           "action": "Quarantined: never passed to later agents"})
        run.log("site_survey", {"photo": s.photo_name, "area_type": s.area_type, "surface": s.surface,
                                "soil": s.soil_level, "confidence": s.confidence, "injection_hits": photo_hits})

    # 2. Takeoff (janitorial only). Photo text is deliberately left out of what we pass on.
    takeoff = Takeoff(areas=[])
    if janitorial:
        if demo:
            takeoff = Takeoff(**sample["takeoff"])
        else:
            safe_surveys = [s.model_dump(exclude={"text_seen_in_photo"}) for s in surveys]
            parts = [
                f"Client: {req.client_name}\nBuilding: {req.building_name}\nVisits per week: {req.visits_per_week}",
                "Estimator's known square footage (trusted):\n" + json.dumps([a.model_dump() for a in req.areas], indent=1),
                "Photo surveys:\n" + json.dumps(safe_surveys, indent=1),
                wrap_untrusted("ESTIMATOR NOTES", req.estimator_notes or "(none)"),
            ]
            if floor_plan:
                plan_small, _ = shrink_photo(floor_plan[1])
                parts += ["Floor plan image follows.", BinaryContent(data=plan_small, media_type="image/jpeg")]
            takeoff = await run.call(agents.build_takeoff_agent(model_for(model, "takeoff")), "takeoff", parts)

        # Known sizes always win, whatever the agent returned.
        known = {a.name.strip().lower(): a for a in req.areas}
        for a in takeoff.areas:
            k = known.get(a.name.strip().lower())
            if k and abs(k.sq_ft - a.sq_ft) > 0.5:
                run.log("guard", {"note": f"Takeoff changed '{a.name}' from {k.sq_ft} to {a.sq_ft} sq ft; restored estimator value."})
                a.sq_ft = k.sq_ft
        run.log("takeoff", {"areas": len(takeoff.areas), "assumptions": takeoff.assumptions})
        if not takeoff.areas:
            raise ValueError("The Takeoff agent found no areas to price. Add square footage or clearer photos.")

    # 3. Pricing (code) and 4. Bid Checker (code).
    priced = price_bid(takeoff.areas, req.visits_per_week, req.hourly_wage, req.costs) if janitorial else None
    fc = price_floor_care(req.floor_care, req.floor_care_prices) if floor and req.floor_care else None
    flags = check_bid(priced, takeoff.areas, [s.model_dump() for s in surveys], req.floor_care)
    run.log("pricing", {"janitorial_monthly": priced and priced["monthly_price"],
                        "per_sqft": priced and priced["price_per_sqft_month"],
                        "floor_care_first_year": fc and fc["first_year_total"]})
    run.log("bid_checker", {"flags": flags})

    # 5. Proposal Writer (words only).
    if demo:
        if janitorial:
            proposal = Proposal(**sample["proposal"])
        else:
            proposal = Proposal(cover_letter=demo_floor_letter(req), scope_of_work=[],
                                exclusions=["Floor repairs or tile replacement", "Moving heavy furniture or equipment",
                                            "Biohazard cleanup"])
    else:
        specials = sorted({x for s in surveys for x in s.special_services})
        info = [f"Client: {req.client_name}\nBuilding: {req.building_name}\nServices: {req.service_type.replace('_', ' ')}"]
        if janitorial:
            info += [f"Janitorial visits per week: {req.visits_per_week}",
                     "Janitorial areas:\n" + json.dumps([{k: l[k] for k in ("name", "area_type", "surface", "sq_ft")} for l in priced["lines"]], indent=1)]
        if fc:
            info.append("Floor care services (scope lines are added by code; just mention them in the letter):\n" + json.dumps(
                [{k: l[k] for k in ("name", "service_label", "sq_ft", "frequency_label")} for l in fc["lines"]], indent=1))
        info.append("Periodic work seen on the walkthrough: " + (", ".join(specials) or "none"))
        proposal = await run.call(agents.build_proposal_agent(model_for(model, "proposal_writer")), "proposal_writer", "\n\n".join(info))

    # Code-owned parts of the proposal.
    if fc:
        proposal.scope_of_work = [x for x in proposal.scope_of_work if not x.lower().startswith("floor care")] + floor_care_scope(fc)
        sold = {l["service"] for l in fc["lines"]}
        proposal.exclusions = [x for x in proposal.exclusions
                               if not any(w in x.lower() for svc in sold for w in FLOOR_EXCLUSION_WORDS[svc])]
    if scan_dollars(proposal.cover_letter):
        run.log("guard", {"note": "Proposal letter contained dollar figures; removed. Prices come only from the pricing table."})
        proposal.cover_letter = strip_dollars(proposal.cover_letter)
    run.log("proposal_writer", {"scope_items": len(proposal.scope_of_work)})

    append_usage(run.usage)
    bid = {
        "id": bid_id, "status": "draft", "demo_mode": demo, "created": datetime.now().isoformat(timespec="seconds"),
        "service_type": req.service_type,
        "request": req.model_dump(), "photos": [p[0] for p in photos],
        "surveys": [s.model_dump() for s in surveys], "takeoff": takeoff.model_dump(),
        "pricing": priced, "floor_care": fc, "totals": bid_totals(priced, fc),
        "flags": flags, "injection_alerts": alerts,
        "proposal": proposal.model_dump(), "usage": run.usage, "usage_total": summarize_usage(run.usage),
        "audit": run.audit,
    }
    save_bid(bid)
    return bid


def bid_totals(priced: dict | None, fc: dict | None) -> dict:
    jan_month = priced["monthly_price"] if priced else 0
    return {
        "janitorial_monthly": jan_month,
        "janitorial_annual": priced["annual_price"] if priced else 0,
        "floor_care_one_time": fc["one_time_total"] if fc else 0,
        "floor_care_recurring_annual": fc["recurring_annual_total"] if fc else 0,
        "first_year_value": round((priced["annual_price"] if priced else 0) + (fc["first_year_total"] if fc else 0), 2),
    }


_DOLLARS = re.compile(r"\$\s?\d[\d,]*(\.\d+)?")


def scan_dollars(text: str) -> bool:
    return bool(_DOLLARS.search(text or ""))


def strip_dollars(text: str) -> str:
    return _DOLLARS.sub("[see pricing summary]", text)


def summarize_usage(usage: list[dict]) -> dict:
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "requests", "tool_calls", "cost_usd", "seconds")
    total = {k: round(sum(u[k] for u in usage), 6) for k in keys}
    total["calls"] = len(usage)
    return total


def save_bid(bid: dict) -> None:
    (BIDS_DIR / f"{bid['id']}.json").write_text(json.dumps(bid, indent=2), encoding="utf-8")


def load_bid(bid_id: str) -> dict:
    if not re.fullmatch(r"[\w-]{6,40}", bid_id):
        raise FileNotFoundError(bid_id)
    return json.loads((BIDS_DIR / f"{bid_id}.json").read_text(encoding="utf-8"))


def list_bids() -> list[dict]:
    out = []
    for p in sorted(BIDS_DIR.glob("*.json"), reverse=True):
        b = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": b["id"], "client": b["request"]["client_name"], "building": b["request"]["building_name"],
                    "status": b["status"], "service_type": b.get("service_type", "janitorial"),
                    "first_year_value": b["totals"]["first_year_value"], "created": b["created"],
                    "demo_mode": b.get("demo_mode", False)})
    return out


__all__ = ["run_bid", "load_bid", "list_bids", "save_bid", "BudgetExceeded"]


# ======================= Revision agent and Past-Bid Comparison =======================
from . import benchmark as bench  # noqa: E402
from . import revise as rev  # noqa: E402


def _live_model(model):
    return None if (DEMO_MODE and model is None) else (model or agents.get_model())


async def revise_bid(bid_id: str, message: str, model=None) -> dict:
    bid = load_bid(bid_id)
    run = Run(bid_id)
    st = rev.BidState(bid)
    before = dict(bid["totals"])
    hits = scan_for_injection(message)
    m = _live_model(model)
    if m is None:
        result = rev.rule_based_revision(message, st)
    else:
        agent = rev.build_revision_agent(model_for(m, "revision"), st)
        result = await run.call(agent, "revision", wrap_untrusted("MANAGER REQUEST", message))

    jan = bid.get("service_type", "janitorial") in ("janitorial", "both")
    if jan and not st.areas:
        raise ValueError("A janitorial bid needs at least one area; that change would remove them all.")
    priced = price_bid(st.areas, st.visits, st.wage, bid["request"].get("costs")) if jan else None
    fc = price_floor_care(st.floor, st.floor_prices) if st.floor else None
    bid["request"].update(visits_per_week=st.visits, hourly_wage=st.wage,
                          floor_care=[f.model_dump() for f in st.floor], floor_care_prices=st.floor_prices)
    bid["takeoff"]["areas"] = [a.model_dump() for a in st.areas]
    bid["pricing"], bid["floor_care"], bid["totals"] = priced, fc, bid_totals(priced, fc)
    bid["flags"] = check_bid(priced, st.areas, bid["surveys"], st.floor)
    bid["proposal"]["scope_of_work"] = [x for x in bid["proposal"]["scope_of_work"]
                                        if not x.lower().startswith("floor care")] + floor_care_scope(fc)
    if bid["status"] == "approved":
        bid["audit"].append({"time": rev.now(), "step": "approval_reset", "note": "Bid changed after approval; needs a new approval."})
    bid["status"] = "draft"
    bid.pop("decision", None)
    append_usage(run.usage)
    entry = {"time": rev.now(), "message": message, "summary": result.summary,
             "changes": st.log, "agent_changes": result.changes, "injection_hits": hits,
             "before": before, "after": bid["totals"], "usage": summarize_usage(run.usage), "demo_mode": m is None}
    bid.setdefault("revisions", []).append(entry)
    bid["usage"] += run.usage
    bid["usage_total"] = summarize_usage(bid["usage"])
    bid["audit"].append({"time": entry["time"], "step": "revision", "message": message, "changes": st.log})
    save_bid(bid)
    return bid


async def benchmark_bid(bid_id: str, model=None) -> dict:
    bid = load_bid(bid_id)
    past = bench.load_past_bids()
    run = Run(bid_id)
    m = _live_model(model)
    if m is None or not bid.get("pricing"):
        report = bench.rule_based_report(bid, past)
    else:
        agent = bench.build_benchmark_agent(model_for(m, "benchmark"), bid, past)
        facts = bench.this_bid_facts(bid)
        report = await run.call(agent, "benchmark", "New bid summary:\n" + json.dumps(facts, indent=1))
    details = [bench.compare(bid, p) for p in past if p["id"] in report.compared_with] if bid.get("pricing") else []
    append_usage(run.usage)
    bid["benchmark"] = {"time": rev.now(), **report.model_dump(), "details": details,
                        "usage": summarize_usage(run.usage), "demo_mode": m is None}
    bid["usage"] += run.usage
    bid["usage_total"] = summarize_usage(bid["usage"])
    bid["audit"].append({"time": rev.now(), "step": "benchmark", "verdict": report.verdict, "compared_with": report.compared_with})
    save_bid(bid)
    return bid


# ======================= What-if pricing (no AI: pure code) =======================
from .models import Area, FloorCareItem  # noqa: E402
from .pricing import what_if  # noqa: E402


def bid_areas(bid: dict) -> list[Area]:
    return [Area(**a) for a in bid["takeoff"]["areas"]]


def what_if_bid(bid_id: str, hourly_wage: float | None, visits: int | None, costs: dict | None) -> dict:
    bid = load_bid(bid_id)
    if not bid.get("pricing"):
        raise ValueError("This bid has no janitorial pricing to model.")
    rq = bid["request"]
    merged = {**rq.get("costs", {}), **(costs or {})}
    return what_if(bid_areas(bid), visits or rq["visits_per_week"], hourly_wage or bid["pricing"]["hourly_wage"], merged)


def apply_costs(bid_id: str, hourly_wage: float, visits: int, costs: dict, who: str = "manager") -> dict:
    """Save what-if settings onto the bid. Re-prices in code, resets approval, logs before/after."""
    bid = load_bid(bid_id)
    if not bid.get("pricing"):
        raise ValueError("This bid has no janitorial pricing to change.")
    if not (7.25 <= hourly_wage <= 60) or not (1 <= visits <= 7):
        raise ValueError("Wage must be $7.25-$60 and visits 1-7 per week.")
    before = dict(bid["totals"])
    areas = bid_areas(bid)
    priced = price_bid(areas, visits, hourly_wage, costs)
    defaults = {k: v for k, v in priced["costs"].items()}
    bid["request"].update(hourly_wage=hourly_wage, visits_per_week=visits, costs=costs)
    bid["pricing"], bid["totals"] = priced, bid_totals(priced, bid.get("floor_care"))
    bid["flags"] = check_bid(priced, areas, bid["surveys"], [FloorCareItem(**f) for f in bid["request"].get("floor_care", [])])
    if bid["status"] == "approved":
        bid["audit"].append({"time": rev.now(), "step": "approval_reset", "note": "Bid changed after approval; needs a new approval."})
    bid["status"] = "draft"
    bid.pop("decision", None)
    entry = {"time": rev.now(), "message": f"What-if settings applied by {who}", "summary": "Cost settings changed in the What-if panel.",
             "changes": [f"wage ${hourly_wage:.2f}", f"{visits} visits/week"] + [f"{k} = {v}" for k, v in costs.items()],
             "agent_changes": [], "injection_hits": [], "before": before, "after": bid["totals"],
             "usage": summarize_usage([]), "demo_mode": True, "costs_used": defaults}
    bid.setdefault("revisions", []).append(entry)
    bid["audit"].append({"time": entry["time"], "step": "what_if_applied", "changes": entry["changes"]})
    save_bid(bid)
    return bid
