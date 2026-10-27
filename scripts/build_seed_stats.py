"""
Build src/fourtake/seed_stats.json, the evidence a new machine's model ranking starts from.

Run it on the maintainer's machine before a release, and read the diff before committing it:

    uv run python scripts/build_seed_stats.py                      # fetch the catalogue, write the seed
    uv run python scripts/build_seed_stats.py --catalogue models.json --out /tmp/seed.json

It reads this machine's telemetry (~/.cache/4take/stats.json) and keeps, for each model the
OpenRouter catalogue still lists as free and usable for review, three numbers: requests sent,
requests answered with a review, and the median latency of an answer. See fourtake/seed.py.
"""

import argparse
import json
import pathlib
import sys
from datetime import date

import httpx

from fourtake import discovery, seed, telemetry

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOGUE_URL = "https://openrouter.ai/api/v1/models"


def live_free(catalogue: dict) -> set[str]:
    """Catalogue ids that discovery would offer: free, text out, `max_tokens` accepted."""
    return {
        m["id"]
        for m in catalogue.get("data", [])
        if m.get("id") and discovery.is_model_free(m) and discovery.is_model_compatible(m["id"], m)
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", type=pathlib.Path, help="a saved catalogue instead of fetching it")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "src" / "fourtake" / seed.SEED_FILE)
    args = ap.parse_args()

    if args.catalogue:
        catalogue = json.loads(args.catalogue.read_text(encoding="utf-8"))
    else:
        response = httpx.get(CATALOGUE_URL, timeout=30)
        response.raise_for_status()
        catalogue = response.json()

    path = telemetry.stats_path()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"no telemetry at {path}: nothing to build a seed from", file=sys.stderr)
        return 1
    events = stored.get("events", []) if isinstance(stored, dict) else stored

    content = seed.build(telemetry.aggregate(path), live_free(catalogue), events, date.today().isoformat())
    args.out.write_text(
        json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )

    print(
        f"{len(content['models'])} models from {content['events']} events, {content['window']}, -> {args.out}"
    )
    for model, row in sorted(
        content["models"].items(), key=lambda kv: -kv[1]["successes"] / kv[1]["attempts"]
    ):
        rate = row["successes"] / row["attempts"]
        print(f"  {model.removeprefix('openrouter/'):<55} {row['attempts']:>4} sent  {rate:>4.0%} answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
