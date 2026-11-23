"""Past-Bid Comparison agent: checks a new bid against CFM's real past bids.

Code does every comparison number (compare_to_past_bid); the agent only picks which past
bids are comparable and explains the result in plain English.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .config import DATA_DIR, PROMPTS_DIR, RATES

Verdict = Literal["in_line", "below_past_bids", "above_past_bids", "not_comparable"]


class BenchmarkReport(BaseModel):
    compared_with: list[str] = Field(default_factory=list, description="ids of the past bids used")
    verdict: Verdict
    explanation: str = Field(..., max_length=1500)
    suggestions: list[str] = Field(default_factory=list)


def load_past_bids() -> list[dict]:
    for name in ("past_bids.json", "past_bids.sample.json"):
        p = DATA_DIR / name
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))["bids"]
    return []


def this_bid_facts(bid: dict) -> dict:
    p = bid.get("pricing") or {}
    sq = p.get("total_sq_ft") or 0
    return {
        "client": bid["request"]["client_name"],
        "area_types": sorted({l["area_type"] for l in p.get("lines", [])}),
        "sq_ft": sq,
        "visits_per_week": p.get("visits_per_week"),
        "hourly_wage": p.get("hourly_wage"),
        "monthly_price": p.get("monthly_price"),
        "price_per_sqft_month": p.get("price_per_sqft_month"),
        "hours_per_visit": p.get("hours_per_visit"),
        "hours_per_1000_sqft_per_visit": round(p["hours_per_visit"] / sq * 1000, 3) if sq else None,
        "profit_pct": (p.get("costs") or {}).get("profit_pct", RATES["labor"]["profit_pct"]),
    }


def compare(bid: dict, past: dict) -> dict:
    t = this_bid_facts(bid)
    wpm = past.get("calc_weeks_per_month", RATES["weeks_per_month"])   # CFM's calculator counts 4 weeks a month
    past_hpv = past["labor_hours_month"] / (past["visits_per_week"] * wpm)
    past_h1000 = past_hpv / past["sq_ft"] * 1000
    pct = lambda a, b: round((a - b) / b * 100, 1) if a and b else None  # noqa: E731
    return {
        "past_id": past["id"], "past_name": past["name"], "past_building_type": past["building_type"],
        "past_schedule": past["schedule"],
        "sq_ft": {"this": t["sq_ft"], "past": past["sq_ft"]},
        "visits_per_week": {"this": t["visits_per_week"], "past": past["visits_per_week"]},
        "price_per_sqft_month": {"this": t["price_per_sqft_month"], "past": past["price_per_sqft_month"],
                                 "difference_pct": pct(t["price_per_sqft_month"], past["price_per_sqft_month"])},
        "hours_per_1000_sqft_per_visit": {"this": t["hours_per_1000_sqft_per_visit"], "past": round(past_h1000, 3),
                                          "difference_pct": pct(t["hours_per_1000_sqft_per_visit"], past_h1000)},
        "hourly_wage": {"this": t["hourly_wage"], "past": past["hourly_wage"]},
        "profit_markup": {"this": t["profit_pct"], "past": past["profit_pct"]},
    }


def rule_based_report(bid: dict, past: list[dict]) -> BenchmarkReport:
    """Used in demo mode (no API key): same numbers, a simple template instead of the AI write-up."""
    if not past or not bid.get("pricing") or not bid["pricing"]["lines"]:
        return BenchmarkReport(verdict="not_comparable", explanation="No janitorial pricing or no past bids to compare.")
    c = min((compare(bid, p) for p in past),
            key=lambda c: abs((c["sq_ft"]["this"] or 0) - c["sq_ft"]["past"]) + (0 if c["visits_per_week"]["this"] == c["visits_per_week"]["past"] else 5000)
            + (50000 if "24/7" in c["past_building_type"] and c["visits_per_week"]["this"] != 7 else 0))
    d = c["price_per_sqft_month"]["difference_pct"] or 0
    verdict = "in_line" if abs(d) <= 25 else ("below_past_bids" if d < 0 else "above_past_bids")
    return BenchmarkReport(
        compared_with=[c["past_id"]], verdict=verdict,
        explanation=(f"Closest past bid: {c['past_name']} ({c['sq_ft']['past']:,} sq ft, {c['visits_per_week']['past']} visits/week). "
                     f"This bid is ${c['price_per_sqft_month']['this']:.3f}/sq ft/month vs ${c['price_per_sqft_month']['past']:.3f} "
                     f"({d:+.1f}%). Labor is {c['hours_per_1000_sqft_per_visit']['this']} vs {c['hours_per_1000_sqft_per_visit']['past']} "
                     "hours per 1,000 sq ft per visit."),
        suggestions=[f"Past bid used a {c['profit_markup']['past']:.0%} profit markup; this bid uses {c['profit_markup']['this']:.0%}."]
        if c["profit_markup"]["past"] != c["profit_markup"]["this"] else [],
    )


def build_benchmark_agent(model, bid: dict, past: list[dict]) -> Agent:
    agent = Agent(model, name="benchmark", output_type=BenchmarkReport,
                  instructions=(PROMPTS_DIR / "benchmark.md").read_text(encoding="utf-8"),
                  model_settings={"openai_prompt_cache_key": "cfm-bid-builder-benchmark"}, retries=1)
    state = {"bid": bid, "past": past}

    @agent.tool_plain
    def list_past_bids() -> list[dict]:
        """List CFM's past bids: id, name, building type, size, visits per week, schedule."""
        return [{k: p[k] for k in ("id", "name", "building_type", "sq_ft", "visits_per_week", "schedule")}
                for p in state["past"]]

    @agent.tool_plain
    def compare_to_past_bid(past_id: str) -> dict:
        """Exact side-by-side numbers for this bid vs one past bid (price per sq ft, labor hours, wage, profit)."""
        for p in state["past"]:
            if p["id"] == past_id:
                return compare(state["bid"], p)
        return {"error": f"No past bid with id {past_id}"}

    return agent
