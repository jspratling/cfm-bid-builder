"""Revision agent: change a finished bid by plain-English request, then re-price with code."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .config import PROMPTS_DIR, RATES
from .models import Area, FloorCareItem


class RevisionResult(BaseModel):
    summary: str = Field(..., max_length=600)
    changes: list[str] = Field(default_factory=list)


class BidState:
    """A working copy of the bid's inputs that the tools edit."""

    def __init__(self, bid: dict):
        rq = bid["request"]
        p = bid.get("pricing") or {}
        self.visits = p.get("visits_per_week") or rq.get("visits_per_week", 5)
        self.wage = p.get("hourly_wage") or rq.get("hourly_wage") or RATES["labor"]["default_hourly_wage"]
        self.areas = [Area(**a) for a in bid["takeoff"]["areas"]]
        self.floor = [FloorCareItem(**f) for f in rq.get("floor_care", [])]
        self.floor_prices = dict(rq.get("floor_care_prices") or {})
        self.log: list[str] = []

    def find(self, items, name: str):
        for i, x in enumerate(items):
            if x.name.strip().lower() == name.strip().lower():
                return i
        return None


def build_revision_agent(model, st: BidState) -> Agent:
    agent = Agent(model, name="revision", output_type=RevisionResult,
                  instructions=(PROMPTS_DIR / "revision.md").read_text(encoding="utf-8"),
                  model_settings={"openai_prompt_cache_key": "cfm-bid-builder-revision"}, retries=1)

    @agent.tool_plain
    def get_bid() -> dict:
        """Current visits per week, hourly wage, janitorial areas and floor-care lines."""
        return {"visits_per_week": st.visits, "hourly_wage": st.wage,
                "areas": [a.model_dump(include={"name", "area_type", "surface", "sq_ft", "soil_level"}) for a in st.areas],
                "floor_care": [f.model_dump() for f in st.floor], "floor_care_prices": st.floor_prices}

    @agent.tool_plain
    def set_visits_per_week(visits: int) -> str:
        """Change janitorial visits per week (1-7)."""
        if not 1 <= visits <= 7:
            return "Rejected: visits must be 1-7."
        st.log.append(f"Visits per week {st.visits} -> {visits}"); st.visits = visits
        return "ok"

    @agent.tool_plain
    def set_hourly_wage(wage: float) -> str:
        """Change the hourly pay rate for this job."""
        lo, hi = RATES["labor"]["min_hourly_wage"], RATES["labor"]["max_hourly_wage"]
        if not lo <= wage <= hi:
            return f"Rejected: wage must be ${lo}-${hi}."
        st.log.append(f"Hourly wage ${st.wage:.2f} -> ${wage:.2f}"); st.wage = wage
        return "ok"

    @agent.tool_plain
    def set_area_sq_ft(name: str, sq_ft: float) -> str:
        """Change the square footage of an existing area (exact name)."""
        i = st.find(st.areas, name)
        if i is None:
            return f"No area named {name!r}."
        if not 0 < sq_ft <= 2_000_000:
            return "Rejected: bad square footage."
        st.log.append(f"{st.areas[i].name}: {st.areas[i].sq_ft:,.0f} -> {sq_ft:,.0f} sq ft")
        st.areas[i].sq_ft = sq_ft; st.areas[i].source = "manager revision"
        return "ok"

    @agent.tool_plain
    def add_area(name: str, area_type: str, surface: str, sq_ft: float, soil_level: str = "normal") -> str:
        """Add a janitorial area. area_type/surface/soil must be valid values (see list in get_bid areas)."""
        try:
            a = Area(name=name, area_type=area_type, surface=surface, sq_ft=sq_ft, soil_level=soil_level, source="manager revision")
        except Exception as e:  # noqa: BLE001
            return f"Rejected: {e}"
        if st.find(st.areas, name) is not None:
            return "An area with that name already exists."
        st.areas.append(a); st.log.append(f"Added area {name} ({area_type}, {surface}, {sq_ft:,.0f} sq ft)")
        return "ok"

    @agent.tool_plain
    def remove_area(name: str) -> str:
        """Remove a janitorial area (exact name)."""
        i = st.find(st.areas, name)
        if i is None:
            return f"No area named {name!r}."
        st.log.append(f"Removed area {st.areas.pop(i).name}")
        return "ok"

    @agent.tool_plain
    def add_floor_care(name: str, service: Literal["strip_wax", "carpet_clean"], sq_ft: float,
                       frequency: Literal["one_time", "annual", "semiannual", "quarterly", "monthly"] = "one_time") -> str:
        """Add a floor-care line (strip_wax for VCT, carpet_clean for carpet)."""
        try:
            f = FloorCareItem(name=name, service=service, sq_ft=sq_ft, frequency=frequency)
        except Exception as e:  # noqa: BLE001
            return f"Rejected: {e}"
        st.floor.append(f); st.log.append(f"Added floor care: {name} {service} {sq_ft:,.0f} sq ft {frequency}")
        return "ok"

    @agent.tool_plain
    def remove_floor_care(name: str) -> str:
        """Remove a floor-care line (exact name)."""
        i = st.find(st.floor, name)
        if i is None:
            return f"No floor-care line named {name!r}."
        st.log.append(f"Removed floor care {st.floor.pop(i).name}")
        return "ok"

    @agent.tool_plain
    def set_floor_care_price(service: Literal["strip_wax", "carpet_clean"], price_per_sqft: float) -> str:
        """Change the per-square-foot price for strip_wax or carpet_clean on this bid."""
        if not 0.05 <= price_per_sqft <= 20:
            return "Rejected: price must be $0.05-$20 per sq ft."
        st.floor_prices[service] = price_per_sqft; st.log.append(f"{service} price -> ${price_per_sqft:.2f}/sq ft")
        return "ok"

    return agent


def rule_based_revision(message: str, st: BidState) -> RevisionResult:
    """Demo mode (no API key): handles 'N nights/visits' and '$X/hr' only."""
    import re
    m = re.search(r"(\d)\s*(nights?|visits?|days?)", message, re.I)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 7:
            st.log.append(f"Visits per week {st.visits} -> {v}"); st.visits = v
    w = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*(/\s*h|an hour|per hour|/hr)", message, re.I)
    if w:
        st.log.append(f"Hourly wage ${st.wage:.2f} -> ${float(w.group(1)):.2f}"); st.wage = float(w.group(1))
    return RevisionResult(summary="Demo mode: applied simple changes." if st.log else
                          "Demo mode understands only visits per week and $/hr. Add an API key for full revisions.",
                          changes=list(st.log))


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")
