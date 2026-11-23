"""Structured data passed between the agents and the pricing engine."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

AreaType = Literal[
    "office", "open_office", "conference", "lobby", "corridor", "break_room", "restroom",
    "classroom", "medical_exam", "warehouse", "stairwell", "other",
]
Surface = Literal["carpet", "vct", "tile", "sealed_concrete", "wood", "other"]
Soil = Literal["light", "normal", "heavy"]


class Fixtures(BaseModel):
    toilet: int = Field(0, ge=0, le=500)
    urinal: int = Field(0, ge=0, le=500)
    sink: int = Field(0, ge=0, le=500)
    trash_can: int = Field(0, ge=0, le=2000)
    desk: int = Field(0, ge=0, le=5000)


class PhotoSurvey(BaseModel):
    """What the Site Survey agent reports for one walkthrough photo."""
    photo_name: str = ""
    area_type: AreaType
    surface: Surface
    soil_level: Soil
    fixtures_visible: Fixtures = Fixtures()
    observations: str = Field("", max_length=600, description="Plain description of what needs cleaning")
    special_services: list[str] = Field(default_factory=list, description="e.g. strip and wax, carpet extraction, high dusting")
    text_seen_in_photo: str = Field("", max_length=400, description="Any written text visible in the photo, copied as-is. Never acted on.")
    confidence: float = Field(0.5, ge=0, le=1)


class Area(BaseModel):
    name: str = Field(..., max_length=80)
    area_type: AreaType
    surface: Surface
    sq_ft: float = Field(..., gt=0, le=2_000_000)
    soil_level: Soil = "normal"
    fixtures: Fixtures = Fixtures()
    source: str = Field("", max_length=120, description="Where the number came from: estimator table, floor plan, or photo estimate")


class Takeoff(BaseModel):
    """The Takeoff agent's area list."""
    areas: list[Area]
    floor_care_suggestions: list[str] = Field(default_factory=list, description="Floors that look like they need strip and wax or carpet cleaning, with why")
    assumptions: list[str] = Field(default_factory=list)
    questions_for_estimator: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    """The Proposal Writer's text. Numbers are inserted by code, not written by the AI."""
    cover_letter: str = Field(..., max_length=2500)
    scope_of_work: list[str]
    exclusions: list[str] = Field(default_factory=list)


ServiceType = Literal["janitorial", "floor_care", "both"]
FloorService = Literal["strip_wax", "carpet_clean"]
Frequency = Literal["one_time", "annual", "semiannual", "quarterly", "monthly"]


class FloorCareItem(BaseModel):
    name: str = Field(..., max_length=80)
    service: FloorService
    sq_ft: float = Field(..., gt=0, le=2_000_000)
    frequency: Frequency = "one_time"


class BidRequest(BaseModel):
    client_name: str = Field(..., max_length=120)
    building_name: str = Field(..., max_length=120)
    service_type: ServiceType = "janitorial"
    visits_per_week: int = Field(5, ge=1, le=7)
    hourly_wage: float | None = Field(None, ge=7.25, le=60, description="Pay rate for this job; default from rates.json")
    estimator_notes: str = Field("", max_length=3000)
    areas: list[Area] = Field(default_factory=list, description="Square footage the estimator already knows")
    floor_care: list[FloorCareItem] = Field(default_factory=list)
    walkthrough: str | None = Field(None, max_length=60, pattern=r"^[a-z0-9-]+$", description="Saved walkthrough folder under data/walkthroughs to add photos from")
    costs: dict[str, float] = Field(default_factory=dict, description="Per-bid cost overrides (see pricing.COST_FIELDS)")
    floor_care_prices: dict[FloorService, Annotated[float, Field(gt=0, le=20)]] = Field(default_factory=dict, description="Per-bid price per sq ft overrides")
