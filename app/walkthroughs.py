"""Saved walkthroughs: a folder of photos (and an optional walkthrough.json) under data/walkthroughs/<slug>/.

Photo file names start with the room, e.g. "women-restroom__IMG_1116.jpg", so the agents
can tell which photos belong to the same space.
"""
from __future__ import annotations

import json
import re

from .config import DATA_DIR

WALK_DIR = DATA_DIR / "walkthroughs"
PHOTO_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
SLUG = re.compile(r"^[a-z0-9-]+$")


def _folder(slug: str):
    if not SLUG.fullmatch(slug or ""):
        raise FileNotFoundError(slug)
    d = WALK_DIR / slug
    if not d.is_dir():
        raise FileNotFoundError(slug)
    return d


def photo_files(slug: str):
    return sorted(p for p in _folder(slug).iterdir() if p.suffix.lower() in PHOTO_EXT)


def info(slug: str) -> dict:
    d = _folder(slug)
    meta = {}
    if (d / "walkthrough.json").is_file():
        meta = json.loads((d / "walkthrough.json").read_text(encoding="utf-8"))
    files = photo_files(slug)
    rooms = sorted({p.name.split("__")[0].replace("-", " ") for p in files if "__" in p.name})
    return {"slug": slug, "photos": [p.name for p in files], "rooms": rooms, **meta}


def list_all() -> list[dict]:
    if not WALK_DIR.is_dir():
        return []
    out = []
    for d in sorted(WALK_DIR.iterdir()):
        if d.is_dir() and SLUG.fullmatch(d.name):
            i = info(d.name)
            out.append({"slug": d.name, "title": i.get("title", d.name), "photos": len(i["photos"]), "rooms": i["rooms"]})
    return out


def load_photos(slug: str) -> list[tuple[str, bytes, str]]:
    return [(p.name, p.read_bytes(), PHOTO_EXT[p.suffix.lower()]) for p in photo_files(slug)]


def photo_path(slug: str, name: str):
    for p in photo_files(slug):
        if p.name == name:
            return p
    raise FileNotFoundError(name)
