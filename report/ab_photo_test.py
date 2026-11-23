"""A/B test: does shrinking photos to 1568 px hurt the Site Survey agent?

Downloads public, freely licensed photos of commercial interiors from Wikimedia Commons
(large originals, 2,500 px or wider), then surveys each photo twice with the real Site Survey
agent: once at full size, once shrunk the way the app does it (1568 px, JPEG q82).
Compares tokens, cost and the answers (room type, floor, soil, fixture counts, text read).

Run from the repo folder:   .venv\\Scripts\\python report\\ab_photo_test.py
Needs a key in .env (here or one folder up). Uses about 100k tokens (about 2 cents).
Writes report/ab_test/ab_results.json and small thumbnails for the report.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402
from pydantic_ai import BinaryContent  # noqa: E402

from app import agents  # noqa: E402
from app.config import DEMO_MODE, MODEL_NAME  # noqa: E402
from app.images import shrink_photo  # noqa: E402
from app.pipeline import usage_record  # noqa: E402
from app.safety import append_usage, check_daily_budget  # noqa: E402

OUT = ROOT / "report" / "ab_test"
UA = {"User-Agent": "CFM-Bid-Builder-class-project/1.0 (Yale MGT 409; jspratling@users.noreply.github.com)"}
QUERIES = ["public restroom interior", "office interior desks", "break room kitchen office",
           "school corridor hallway", "hospital corridor", "conference room interior",
           "lobby interior building", "restroom sinks"]
WANT = 8
MIN_WIDTH = 2500


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()[:120]


def find_photos(client: httpx.Client) -> list[dict]:
    picked, seen = [], set()
    for q in QUERIES:
        r = client.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"filetype:bitmap {q}", "gsrlimit": 15,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata"})
        pages = (r.json().get("query") or {}).get("pages") or {}
        for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("mime") != "image/jpeg" or ii.get("width", 0) < MIN_WIDTH or ii.get("size", 0) > 15_000_000:
                continue
            if ii["url"] in seen:
                continue
            meta = ii.get("extmetadata") or {}
            seen.add(ii["url"])
            picked.append({"query": q, "title": p["title"], "url": ii["url"],
                           "page": ii.get("descriptionurl", ""), "width": ii["width"], "height": ii["height"],
                           "license": strip_html((meta.get("LicenseShortName") or {}).get("value")),
                           "author": strip_html((meta.get("Artist") or {}).get("value"))})
            break  # one photo per query, so the set covers different room types
        if len(picked) >= WANT:
            break
    return picked


def survey_dict(s) -> dict:
    d = s.model_dump()
    return {k: d[k] for k in ("area_type", "surface", "soil_level", "fixtures_visible",
                              "text_seen_in_photo", "confidence", "observations", "special_services")}


async def survey(model, name: str, data: bytes, label: str, bid_id: str):
    agent = agents.build_survey_agent(model)
    t0 = time.perf_counter()
    res = await agent.run([f"Survey this walkthrough photo. File name: {name}",
                           BinaryContent(data=data, media_type="image/jpeg")])
    u = res.usage
    rec = usage_record("site_survey", bid_id, u, time.perf_counter() - t0)
    rec["ab_variant"] = label
    return survey_dict(res.output), rec


async def main():
    if DEMO_MODE:
        sys.exit("No API key found (.env here or one folder up). This test needs a real key.")
    check_daily_budget()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "thumbs").mkdir(exist_ok=True)
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as client:
        photos = find_photos(client)
        print(f"Found {len(photos)} large photos on Wikimedia Commons")
        for i, p in enumerate(photos):
            p["bytes"] = client.get(p["url"]).content
            p["name"] = f"ab{i + 1}.jpg"
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(p["bytes"]))).convert("RGB")
            im.thumbnail((480, 480))
            im.save(OUT / "thumbs" / p["name"], "JPEG", quality=80)
            print(f"  {p['name']}: {p['width']}x{p['height']}, {len(p['bytes']) // 1024} KB  {p['title']}")

    model = agents.get_model()
    bid_id = "ab-photo-test"
    gate = asyncio.Semaphore(4)
    usage = []

    async def one(p, label, data):
        async with gate:
            out, rec = await survey(model, p["name"], data, label, bid_id)
            usage.append(rec)
            print(f"  {p['name']} {label}: {rec['input_tokens']} in / {rec['output_tokens']} out, "
                  f"{out['area_type']}, {out['surface']}, {out['soil_level']}")
            return out, rec

    tasks = []
    for p in photos:
        small, info = shrink_photo(p["bytes"])
        p["shrink"] = info
        tasks.append(one(p, "full", p["bytes"]))
        tasks.append(one(p, "shrunk", small))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    rows = []
    for i, p in enumerate(photos):
        full, shrunk = results[2 * i], results[2 * i + 1]
        if isinstance(full, Exception) or isinstance(shrunk, Exception):
            rows.append({"name": p["name"], "error": repr(full if isinstance(full, Exception) else shrunk)})
            continue
        (fo, fr), (so, sr) = full, shrunk
        same = {k: fo[k] == so[k] for k in ("area_type", "surface", "soil_level", "fixtures_visible")}
        rows.append({
            "name": p["name"], "title": p["title"], "page": p["page"], "license": p["license"], "author": p["author"],
            "query": p["query"], "original_px": [p["width"], p["height"]], "original_kb": len(p["bytes"]) // 1024,
            "sent_px_shrunk": p["shrink"].get("sent_px"), "sent_kb_shrunk": p["shrink"].get("sent_kb"),
            "full": {"survey": fo, "input_tokens": fr["input_tokens"], "output_tokens": fr["output_tokens"],
                     "cost_usd": fr["cost_usd"], "seconds": fr["seconds"]},
            "shrunk": {"survey": so, "input_tokens": sr["input_tokens"], "output_tokens": sr["output_tokens"],
                       "cost_usd": sr["cost_usd"], "seconds": sr["seconds"]},
            "same": same,
        })
    append_usage(usage)

    ok = [r for r in rows if "error" not in r]
    summ = {"model": MODEL_NAME, "photos": len(ok), "errors": len(rows) - len(ok)}
    for k in ("area_type", "surface", "soil_level", "fixtures_visible"):
        summ[f"same_{k}"] = sum(r["same"][k] for r in ok)
    for v in ("full", "shrunk"):
        summ[f"{v}_input_tokens"] = sum(r[v]["input_tokens"] for r in ok)
        summ[f"{v}_cost_usd"] = round(sum(r[v]["cost_usd"] for r in ok), 6)
        summ[f"{v}_seconds"] = round(sum(r[v]["seconds"] for r in ok), 1)
        summ[f"{v}_text_read"] = sum(bool(r[v]["survey"]["text_seen_in_photo"]) for r in ok)
        summ[f"{v}_avg_confidence"] = round(sum(r[v]["survey"]["confidence"] for r in ok) / max(len(ok), 1), 3)
    summ["upload_kb_full"] = sum(r["original_kb"] for r in ok)
    summ["upload_kb_shrunk"] = sum(r["sent_kb_shrunk"] or 0 for r in ok)
    out = {"run_at": time.strftime("%Y-%m-%d %H:%M"), "summary": summ, "rows": rows}
    (OUT / "ab_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nSUMMARY")
    print(json.dumps(summ, indent=2))
    print(f"\nSaved {OUT / 'ab_results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
