"""Deterministic pricing engine. The AI never does arithmetic on the bid.

Janitorial (recurring):
  hours per visit = (sq_ft / (rate x surface factor x soil factor) + fixture minutes / 60) x setup/travel factor
  monthly hours   = hours per visit x visits per week x weeks per month
  labor           = monthly hours x hourly wage (adjustable per bid; CFM default $13)
  payroll taxes   = Social Security + Medicare on all wages, plus FUTA and Alabama SUTA on each worker's
                    first $7,000 / $8,000 a year (so the % depends on wage and hours per worker)
  workers' comp   = labor x CFM's policy rate (class 9014)
  burden          = payroll taxes + workers' comp + other benefits
  equipment       = labor x equipment %      (machines and their wear, per account)
  insurance       = labor x insurance %      (general liability and bonding, on top of workers' comp)
  overhead        = (labor + burden) x overhead %
  profit          = (labor + burden + equipment + insurance + overhead) x profit %
  monthly price   = (labor + burden + overhead + profit) / (1 - supplies %)
  supplies        = monthly price x supplies %          (CFM: supplies run about 5% of the monthly charge)

Floor care (per service):
  price per service = sq_ft x price per sq ft (strip and wax VCT $1.25, carpet cleaning $0.85; adjustable per bid)
  annual            = price per service x times per year
"""
from __future__ import annotations

from .config import RATES
from .models import Area, FloorCareItem


def production_rate(area_type: str, surface: str, soil: str) -> float:
    rates = RATES["production_rates_sqft_per_hour"]
    base = rates.get(area_type, rates["other"])
    return base * RATES["surface_speed_factor"].get(surface, 1.0) * RATES["soil_speed_factor"].get(soil, 1.0)


def area_hours_per_visit(area: Area) -> float:
    hours = area.sq_ft / production_rate(area.area_type, area.surface, area.soil_level)
    fixture_minutes = sum(
        count * RATES["minutes_per_fixture"].get(name, 0)
        for name, count in area.fixtures.model_dump().items()
    )
    return (hours + fixture_minutes / 60) * RATES["setup_travel_factor"]


# Every cost driver a manager can change on one bid (the what-if chart and "apply" use these).
COST_FIELDS = {
    # key: (label, min, max)
    "supplies_pct_of_price": ("Cleaning supplies, % of monthly price", 0.0, 0.25),
    "equipment_pct_of_labor": ("Equipment, % of labor", 0.0, 0.25),
    "insurance_pct_of_labor": ("General liability insurance, % of labor", 0.0, 0.10),
    "suta_pct": ("State unemployment (SUTA) rate", 0.0, 0.07),
    "workers_comp_pct": ("Workers' comp, % of labor", 0.0, 0.15),
    "other_benefits_pct": ("Benefits / PTO, % of labor", 0.0, 0.40),
    "overhead_pct": ("Overhead, % of labor + burden", 0.0, 0.40),
    "profit_pct": ("Profit markup", 0.0, 0.60),
    "labor_hours_factor": ("Labor hours vs. ISSA standard", 0.5, 2.0),
}


def cost_defaults() -> dict:
    lab, pr, ac = RATES["labor"], RATES["payroll"], RATES.get("account_costs", {})
    return {
        "supplies_pct_of_price": lab["supplies_pct_of_price"],
        "equipment_pct_of_labor": ac.get("equipment_pct_of_labor", 0.0),
        "insurance_pct_of_labor": ac.get("insurance_pct_of_labor", 0.0),
        "suta_pct": pr["suta_pct"], "workers_comp_pct": pr["workers_comp_pct"],
        "other_benefits_pct": pr["other_benefits_pct"],
        "overhead_pct": lab["overhead_pct"], "profit_pct": lab["profit_pct"], "labor_hours_factor": 1.0,
    }


def resolve_costs(costs: dict | None) -> dict:
    """Defaults plus per-bid overrides; unknown keys and out-of-range values are rejected."""
    c = cost_defaults()
    for k, v in (costs or {}).items():
        if k not in COST_FIELDS:
            raise ValueError(f"Unknown cost setting: {k}")
        _, lo, hi = COST_FIELDS[k]
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f"{COST_FIELDS[k][0]} must be between {lo} and {hi}")
        c[k] = v
    return c


def default_wage() -> float:
    return RATES["labor"]["default_hourly_wage"]


def burden_rates(wage: float, hours_per_week: float | None = None, costs: dict | None = None) -> dict:
    """Employer cost on top of wages, as a share of wages, for one typical worker."""
    pr = {**RATES["payroll"], **{k: v for k, v in (costs or {}).items() if k in ("suta_pct", "workers_comp_pct", "other_benefits_pct")}}
    hrs = hours_per_week or RATES["labor"]["avg_hours_per_employee_week"]
    annual = wage * hrs * 52
    ss = pr["social_security_pct"] * min(annual, pr["social_security_wage_base"]) / annual
    futa = pr["futa_pct"] * min(annual, pr["futa_wage_base"]) / annual
    suta = (pr["suta_pct"] + pr["suta_assessment_pct"]) * min(annual, pr["suta_wage_base"]) / annual
    taxes = ss + pr["medicare_pct"] + futa + suta
    return {
        "annual_wages_per_worker": round(annual, 2), "hours_per_worker_week": hrs,
        "social_security": ss, "medicare": pr["medicare_pct"], "futa": futa, "suta": suta,
        "payroll_taxes": taxes, "workers_comp": pr["workers_comp_pct"], "other_benefits": pr["other_benefits_pct"],
        "total": taxes + pr["workers_comp_pct"] + pr["other_benefits_pct"],
    }


def price_bid(areas: list[Area], visits_per_week: int, hourly_wage: float | None = None,
              costs: dict | None = None) -> dict:
    c = resolve_costs(costs)
    wage = hourly_wage or default_wage()
    wpm = RATES["weeks_per_month"]
    lines = [{
        "name": a.name, "area_type": a.area_type, "surface": a.surface, "soil_level": a.soil_level,
        "sq_ft": round(a.sq_ft), "rate_sqft_per_hour": round(production_rate(a.area_type, a.surface, a.soil_level)),
        "hours_per_visit": round(area_hours_per_visit(a), 2), "source": a.source,
    } for a in areas]
    total_sqft = sum(a.sq_ft for a in areas)
    hours_per_visit = sum(area_hours_per_visit(a) for a in areas)
    monthly_hours = hours_per_visit * c["labor_hours_factor"] * visits_per_week * wpm
    labor = monthly_hours * wage
    br = burden_rates(wage, costs=c)
    payroll_taxes = labor * br["payroll_taxes"]
    workers_comp = labor * br["workers_comp"]
    other_benefits = labor * br["other_benefits"]
    burden = payroll_taxes + workers_comp + other_benefits
    equipment = labor * c["equipment_pct_of_labor"]
    insurance = labor * c["insurance_pct_of_labor"]
    overhead = (labor + burden) * c["overhead_pct"]
    profit = (labor + burden + equipment + insurance + overhead) * c["profit_pct"]
    monthly_price = (labor + burden + equipment + insurance + overhead + profit) / (1 - c["supplies_pct_of_price"])
    supplies = monthly_price * c["supplies_pct_of_price"]
    cost = labor + burden + equipment + insurance + overhead + supplies
    return {
        "lines": lines, "visits_per_week": visits_per_week, "hourly_wage": round(wage, 2),
        "total_sq_ft": round(total_sqft), "hours_per_visit": round(hours_per_visit, 2),
        "monthly_hours": round(monthly_hours, 1),
        "labor": round(labor, 2), "burden": round(burden, 2),
        "payroll_taxes": round(payroll_taxes, 2), "workers_comp": round(workers_comp, 2), "other_benefits": round(other_benefits, 2),
        "burden_rates": {k: (round(v, 5) if isinstance(v, float) else v) for k, v in br.items()}, "overhead": round(overhead, 2),
        "equipment": round(equipment, 2), "insurance": round(insurance, 2), "costs": c,
        "supplies": round(supplies, 2), "cost": round(cost, 2), "profit": round(profit, 2),
        "monthly_price": round(monthly_price, 2), "annual_price": round(monthly_price * 12, 2),
        "price_per_sqft_month": round(monthly_price / total_sqft, 4) if total_sqft else 0,
    }


def floor_care_price(service: str, overrides: dict | None = None) -> float:
    overrides = overrides or {}
    return float(overrides.get(service) or RATES["floor_care"]["services"][service]["price_per_sqft"])


def price_floor_care(items: list[FloorCareItem], overrides: dict | None = None) -> dict:
    svc = RATES["floor_care"]["services"]
    freq = RATES["floor_care"]["frequencies"]
    lines = []
    for it in items:
        rate = floor_care_price(it.service, overrides)
        per = it.sq_ft * rate
        times = freq[it.frequency]["times_per_year"]
        lines.append({
            "name": it.name, "service": it.service, "service_label": svc[it.service]["label"],
            "sq_ft": round(it.sq_ft), "price_per_sqft": round(rate, 4),
            "frequency": it.frequency, "frequency_label": freq[it.frequency]["label"], "times_per_year": times,
            "price_per_service": round(per, 2), "annual_price": round(per * times, 2),
            "recurring": it.frequency != "one_time",
        })
    return {
        "lines": lines,
        "per_service_total": round(sum(l["price_per_service"] for l in lines), 2),
        "one_time_total": round(sum(l["annual_price"] for l in lines if not l["recurring"]), 2),
        "recurring_annual_total": round(sum(l["annual_price"] for l in lines if l["recurring"]), 2),
        "first_year_total": round(sum(l["annual_price"] for l in lines), 2),
        "prices_used": {k: floor_care_price(k, overrides) for k in svc},
    }


def empty_janitorial() -> dict:
    return price_bid([], 1) | {"lines": [], "visits_per_week": 0}


# ---------------- what-if: how the monthly price moves when one cost changes ----------------
SENSITIVITY_STEPS = [
    # (label, field, step) ; field "wage" and "visits" are handled specially
    ("Hourly pay +$1.00", "wage", 1.0),
    ("Labor hours +10%", "labor_hours_factor", 0.10),
    ("Profit markup +5 pts", "profit_pct", 0.05),
    ("Overhead +5 pts", "overhead_pct", 0.05),
    ("Supplies +1 pt", "supplies_pct_of_price", 0.01),
    ("Equipment +1 pt", "equipment_pct_of_labor", 0.01),
    ("Insurance +1 pt", "insurance_pct_of_labor", 0.01),
    ("Workers' comp +1 pt", "workers_comp_pct", 0.01),
    ("SUTA +1 pt", "suta_pct", 0.01),
    ("Benefits / PTO +5 pts", "other_benefits_pct", 0.05),
]


def what_if(areas: list[Area], visits_per_week: int, hourly_wage: float, costs: dict | None = None) -> dict:
    base_costs = resolve_costs(costs)
    base = price_bid(areas, visits_per_week, hourly_wage, base_costs)
    curve = []
    w = 10.0
    while w <= 25.0001:
        curve.append({"wage": round(w, 2), "monthly_price": price_bid(areas, visits_per_week, w, base_costs)["monthly_price"]})
        w += 0.5
    sens = []
    for label, field, step in SENSITIVITY_STEPS:
        if field == "wage":
            p = price_bid(areas, visits_per_week, min(hourly_wage + step, 60), base_costs)
        else:
            _, lo, hi = COST_FIELDS[field]
            p = price_bid(areas, visits_per_week, hourly_wage, {**base_costs, field: min(base_costs[field] + step, hi)})
        sens.append({"label": label, "field": field, "delta": round(p["monthly_price"] - base["monthly_price"], 2)})
    sens.sort(key=lambda x: -abs(x["delta"]))
    return {"priced": base, "wage_curve": curve, "sensitivity": sens,
            "fields": {k: {"label": v[0], "min": v[1], "max": v[2], "default": cost_defaults()[k]} for k, v in COST_FIELDS.items()}}
