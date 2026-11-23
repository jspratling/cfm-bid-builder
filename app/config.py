"""Settings for Bid Builder.

The .env file is looked for in two places, in this order:
  1. this repo's own folder
  2. the folder one level above the repo (where the teaching staff keep theirs)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
OUTPUT_DIR = Path(os.getenv("BID_OUTPUT_DIR") or REPO_DIR / "output")
BIDS_DIR = OUTPUT_DIR / "bids"
PROMPTS_DIR = REPO_DIR / "prompts"


def find_env_file() -> Path | None:
    for folder in (REPO_DIR, REPO_DIR.parent):
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


ENV_FILE = find_env_file()
if ENV_FILE:
    load_dotenv(ENV_FILE, override=False)

PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY", "").strip()
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1").strip()
# Model string as Portkey expects it. Course rule: gpt-6-luna only on the course key.
MODEL_NAME = os.getenv("BID_MODEL", "gpt-6-luna").strip()

# Course rule: the course key is PORTKEY_API_KEY (gpt-6-luna only). A personal key may be used instead,
# named OPENAI_API_KEY. Portkey is used when its key is set, unless AI_PROVIDER=openai.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_want = os.getenv("AI_PROVIDER", "").strip().lower()
if _want == "openai" and OPENAI_API_KEY:
    AI_PROVIDER = "openai"
elif PORTKEY_API_KEY:
    AI_PROVIDER = "portkey"
elif OPENAI_API_KEY:
    AI_PROVIDER = "openai"
else:
    AI_PROVIDER = "none"

# Demo mode runs the whole app with saved sample answers and makes no AI calls.
DEMO_MODE = os.getenv("DEMO_MODE", "").strip() in {"1", "true", "yes"} or AI_PROVIDER == "none"


def load_json(name: str):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


RATES = load_json("rates.json")
LIMITS = RATES["limits"]
TOKEN_PRICES = RATES["token_prices_per_million"]

for d in (OUTPUT_DIR, BIDS_DIR):
    d.mkdir(parents=True, exist_ok=True)
