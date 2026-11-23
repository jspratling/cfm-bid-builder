"""The three AI agents. All run on gpt-6-luna through Portkey."""
from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import AI_PROVIDER, MODEL_NAME, OPENAI_API_KEY, PORTKEY_API_KEY, PORTKEY_BASE_URL, PROMPTS_DIR, RATES
from .models import PhotoSurvey, Proposal, Takeoff
from .pricing import production_rate


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


@lru_cache
def get_model() -> OpenAIResponsesModel:
    """Same Portkey setup as the course homework (HW6): the key goes in the x-portkey-api-key header.
    With AI_PROVIDER=openai (or only OPENAI_API_KEY set) the same model is called on OpenAI directly."""
    if AI_PROVIDER == "openai":
        return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=AsyncOpenAI(api_key=OPENAI_API_KEY)))
    client = AsyncOpenAI(
        api_key="portkey",
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-api-key": PORTKEY_API_KEY},
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def cache_settings(agent_name: str) -> dict:
    """Prompt caching: each agent's fixed instructions are cached and reused across bids and photos."""
    return {"openai_prompt_cache_key": f"cfm-bid-builder-{agent_name}"}


def build_survey_agent(model=None) -> Agent:
    return Agent(
        model or get_model(),
        name="site_survey",
        model_settings=cache_settings("site_survey"),
        output_type=PhotoSurvey,
        instructions=_prompt("site_survey.md"),
        retries=1,
    )


def build_takeoff_agent(model=None) -> Agent:
    agent = Agent(
        model or get_model(),
        name="takeoff",
        model_settings=cache_settings("takeoff"),
        output_type=Takeoff,
        instructions=_prompt("takeoff.md"),
        retries=1,
    )

    @agent.tool_plain
    def list_area_types() -> dict:
        """List valid area types and surfaces with their base cleaning speed (sq ft per hour)."""
        return {
            "area_types": RATES["production_rates_sqft_per_hour"],
            "surfaces": list(RATES["surface_speed_factor"]),
            "soil_levels": list(RATES["soil_speed_factor"]),
        }

    @agent.tool_plain
    def get_production_rate(area_type: str, surface: str, soil_level: str) -> dict:
        """Return the adjusted cleaning speed (sq ft per hour) for one area, to sanity-check a classification."""
        return {"sq_ft_per_hour": round(production_rate(area_type, surface, soil_level))}

    return agent


def build_proposal_agent(model=None) -> Agent:
    return Agent(
        model or get_model(),
        name="proposal_writer",
        model_settings=cache_settings("proposal_writer"),
        output_type=Proposal,
        instructions=_prompt("proposal.md"),
        retries=1,
    )
