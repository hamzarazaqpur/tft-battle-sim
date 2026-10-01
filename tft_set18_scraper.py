#!/usr/bin/env python3
"""
TFT Set 18 (Enchanted Wilds) Data Miner -- Units, Traits, Items & Augments
============================================================================

Pulls the current Teamfight Tactics dataset from Riot's community-run
data mirror and extracts everything playable from a given TFT set --
by default, Set 18 "Enchanted Wilds":

  * Units (champions): cost, traits, base stats, ability text/scaling
  * Traits: description, breakpoint tiers, per-tier bonuses
  * Items (components + completed items + artifacts/emblems): recipe,
    description with numbers filled in, associated/incompatible traits
  * Augments: description with numbers filled in, associated traits

Why this doesn't use the "League of Legends API" with a key
-------------------------------------------------------------
Riot's authenticated developer API (the one requiring a key from
developer.riotgames.com) is built for *dynamic* data: match history,
summoner lookups, league standings, etc. It does not serve static
game-content data like champion stats, item effects, or set rosters.
That static data is published by Riot as "Data Dragon" and mirrored
in a far more complete, structured form by the community-run
Community Dragon (cdragon) project -- the same source nearly every
TFT overlay, tracker, and stats site pulls from. No API key is
needed for either.

Data source: https://raw.communitydragon.org/latest/cdragon/tft/en_us.json

A note on how items/augments are isolated to one set
------------------------------------------------------
Unlike champions and traits, which cdragon conveniently pre-splits
per set (under the "sets" -> "<number>" key), items and augments all
live together in one big top-level "items" list covering *every* TFT
set that has ever existed. To isolate Set 18's items and augments,
this script:

  1. Looks at the leading "TFT<number>_" in each entry's apiName.
     Augments are always tied to the set that introduced them, so an
     apiName prefix of "TFT18_" means it's a Set 18 augment.
  2. Equipment items (components/completed items/artifacts) that have
     NO numbered prefix at all (e.g. "TFT_Item_InfinityEdge") are the
     "universal" item pool that's available across every set, so
     those are always included regardless of which set you ask for.
  3. Equipment items that DO carry a set-numbered prefix (radiant
     items, support items, set-specific artifacts/emblems) are
     included only when the number matches the requested set.

This mirrors how community trackers do it and gets the right answer
in the overwhelming majority of cases, but it's a heuristic rather
than something cdragon labels explicitly -- if a patch changes the
naming convention, adjust `item_set_number()` accordingly.

Usage
-----
    python tft_set18_scraper.py                    # Set 18, ./tft_data
    python tft_set18_scraper.py --set 17            # a different set
    python tft_set18_scraper.py --refresh           # force re-download
    python tft_set18_scraper.py --output-dir out    # custom output dir

Outputs (written to the output directory):
    cdragon_tft_raw.json    - the raw upstream payload (cached locally;
                               delete it or pass --refresh after a patch)
    set{N}_units.json       - full structured detail for every unit
    set{N}_units.csv        - flattened, spreadsheet-friendly summary
    set{N}_traits.json      - full structured detail for every trait
    set{N}_items.json       - full structured detail for every item
    set{N}_items.csv        - flattened, spreadsheet-friendly summary
    set{N}_augments.json    - full structured detail for every augment
    set{N}_augments.csv     - flattened, spreadsheet-friendly summary

No third-party packages required (standard library only).
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CDRAGON_TFT_URL = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"
CDRAGON_CDN_ROOT = "https://raw.communitydragon.org/latest/game/"

_TAG_RE = re.compile(r"<[^>]+>")
_VAR_RE = re.compile(r"@([A-Za-z0-9_.:*]+)@")
_SET_PREFIX_RE = re.compile(r"^TFT(\d+)_")


# --------------------------------------------------------------------------
# Fetching / caching
# --------------------------------------------------------------------------

def fetch_json(url: str, timeout: int = 30, retries: int = 3) -> dict:
    """Download and parse a JSON URL, with a couple of retries."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "tft-set-datamine/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            print(
                f"  fetch attempt {attempt}/{retries} failed ({e}); retrying...",
                file=sys.stderr,
            )
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def load_cdragon_data(cache_path: Path, refresh: bool) -> dict:
    """Load the full cdragon TFT payload, using a local cache unless told not to."""
    if cache_path.exists() and not refresh:
        print(f"Using cached data at {cache_path} (pass --refresh to re-download)")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    print("Downloading current TFT data from Community Dragon...")
    data = fetch_json(CDRAGON_TFT_URL)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    print(f"Saved raw data to {cache_path}")
    return data


# --------------------------------------------------------------------------
# Set isolation
# --------------------------------------------------------------------------

def find_set_champions_and_traits(data: dict, set_number: int):
    """
    Locate the pre-filtered champion/trait payload for a given TFT set.
    cdragon exposes this a couple of ways; try the cleanest first and
    fall back as needed so this keeps working if the schema shifts.
    """
    set_key = str(set_number)

    sets_dict = data.get("sets") or {}
    if set_key in sets_dict:
        entry = sets_dict[set_key]
        champs = entry.get("champions", [])
        traits = entry.get("traits", [])
        if champs:
            return champs, traits

    mutator = f"TFTSet{set_number}"
    for entry in data.get("setData", []) or []:
        if entry.get("mutator") == mutator or entry.get("number") == set_number:
            champs = entry.get("champions", [])
            traits = entry.get("traits", [])
            if champs:
                return champs, traits

    prefix = f"TFT{set_number}_"
    champs = [c for c in data.get("champions", []) or [] if c.get("apiName", "").startswith(prefix)]
    traits = [t for t in data.get("traits", []) or [] if t.get("apiName", "").startswith(prefix)]
    return champs, traits


def item_set_number(api_name):
    """Return the leading TFT<N>_ set number in an apiName, or None if unnumbered."""
    if not api_name:
        return None
    m = _SET_PREFIX_RE.match(api_name)
    return int(m.group(1)) if m else None


def find_items_and_augments(data: dict, set_number: int):
    """
    Split cdragon's global "items" list (which mixes every TFT set ever
    released) into this set's equipment items and this set's augments.
    See the module docstring for the filtering heuristic.
    """
    equipment, augments = [], []
    for item in data.get("items", []) or []:
        num = item_set_number(item.get("apiName"))
        if item.get("isAugment"):
            if num == set_number or num is None:
                augments.append(item)
        else:
            if num is None or num == set_number:
                equipment.append(item)
    return equipment, augments


def build_global_trait_lookup(data: dict) -> dict:
    """apiName -> display name, gathered from every set we can see, so
    associatedTraits/incompatibleTraits on items can be made readable
    regardless of which set originally defined that trait."""
    lookup = {}
    for t in data.get("traits", []) or []:
        if t.get("apiName"):
            lookup[t["apiName"]] = t.get("name")
    for entry in (data.get("sets") or {}).values():
        for t in entry.get("traits", []) or []:
            if t.get("apiName"):
                lookup[t["apiName"]] = t.get("name")
    for entry in data.get("setData", []) or []:
        for t in entry.get("traits", []) or []:
            if t.get("apiName"):
                lookup[t["apiName"]] = t.get("name")
    return lookup


def build_global_item_lookup(data: dict) -> dict:
    """apiName -> display name for every item, used to resolve component
    apiNames inside another item's "composition" list into real names."""
    return {i["apiName"]: i.get("name") for i in data.get("items", []) or [] if i.get("apiName")}


# --------------------------------------------------------------------------
# Text cleanup
# --------------------------------------------------------------------------

def strip_tags(text):
    """Remove the game's inline formatting tags and normalize line breaks."""
    if not text:
        return ""
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _TAG_RE.sub("", text)
    return text.strip()


def _format_value(val):
    if isinstance(val, float):
        val = round(val, 3)
        if val == int(val):
            val = int(val)
    return str(val)


def resolve_ability_text(ability: dict) -> str:
    """
    Champion ability descriptions contain @Variable@ placeholders whose
    values live in ability['variables'] (one array entry per star level).
    Substitute the 1-star value inline for a human-readable string; the
    full per-star arrays are kept separately in the structured output.
    """
    desc = strip_tags(ability.get("desc"))
    variables = {v.get("name"): v.get("value") for v in ability.get("variables", []) if v.get("name")}

    def _sub(match):
        name = match.group(1)
        if name.startswith("TFTUnitProperty"):
            return match.group(0)
        raw = variables.get(name)
        if raw is None:
            return match.group(0)
        val = raw[0] if isinstance(raw, list) and raw else raw
        return _format_value(val)

    return _VAR_RE.sub(_sub, desc)


def resolve_text_with_effects(desc, effects: dict) -> str:
    """
    Item/augment descriptions contain the same @Variable@ style
    placeholders, but backed by a flat effects dict (single value, no
    per-star arrays) instead of a champion ability's variables list.
    """
    desc = strip_tags(desc)
    effects = effects or {}

    def _sub(match):
        name = match.group(1)
        if name.startswith("TFTUnitProperty"):
            return match.group(0)
        val = effects.get(name)
        if val is None:
            return match.group(0)
        return _format_value(val)

    return _VAR_RE.sub(_sub, desc)


def to_cdn_url(path):
    if not path:
        return None
    return CDRAGON_CDN_ROOT + path.lower().lstrip("/")


# --------------------------------------------------------------------------
# Record builders
# --------------------------------------------------------------------------

def build_unit_record(champ: dict) -> dict:
    stats = champ.get("stats", {}) or {}
    ability = champ.get("ability", {}) or {}
    return {
        "apiName": champ.get("apiName"),
        "name": champ.get("name"),
        "cost": champ.get("cost"),
        "traits": champ.get("traits", []),
        "role": champ.get("role"),
        "square_icon": to_cdn_url(champ.get("squareIcon") or champ.get("icon")),
        "tile_icon": to_cdn_url(champ.get("tileIcon")),
        "stats": {
            "health": stats.get("hp"),
            "mana": stats.get("mana"),
            "initial_mana": stats.get("initialMana"),
            "armor": stats.get("armor"),
            "magic_resist": stats.get("magicResist"),
            "attack_damage": stats.get("damage"),
            "attack_speed": stats.get("attackSpeed"),
            "crit_chance": stats.get("critChance"),
            "crit_multiplier": stats.get("critMultiplier"),
            "range": stats.get("range"),
        },
        "ability": {
            "name": ability.get("name"),
            "description": resolve_ability_text(ability),
            "raw_description": ability.get("desc"),
            "icon": to_cdn_url(ability.get("icon")),
            "variables": ability.get("variables", []),
        },
    }


def build_trait_record(trait: dict) -> dict:
    return {
        "apiName": trait.get("apiName"),
        "name": trait.get("name"),
        "description": strip_tags(trait.get("desc")),
        "icon": to_cdn_url(trait.get("icon")),
        "effects": [
            {
                "min_units": e.get("minUnits"),
                "max_units": e.get("maxUnits"),
                "style": e.get("style"),
                "variables": e.get("variables", {}),
            }
            for e in trait.get("effects", [])
        ],
    }


def build_item_record(item: dict, item_lookup: dict, trait_lookup: dict) -> dict:
    effects = item.get("effects", {}) or {}
    composition = item.get("composition", []) or []
    return {
        "apiName": item.get("apiName"),
        "name": item.get("name"),
        "description": resolve_text_with_effects(item.get("desc"), effects),
        "raw_description": item.get("desc"),
        "icon": to_cdn_url(item.get("icon")),
        "unique": bool(item.get("unique")),
        "composition": [
            {"apiName": c, "name": item_lookup.get(c, c)} for c in composition
        ],
        "associated_traits": [trait_lookup.get(t, t) for t in item.get("associatedTraits", [])],
        "incompatible_traits": [trait_lookup.get(t, t) for t in item.get("incompatibleTraits", [])],
        "effects": effects,
    }


def build_augment_record(item: dict, trait_lookup: dict) -> dict:
    effects = item.get("effects", {}) or {}
    return {
        "apiName": item.get("apiName"),
        "name": item.get("name"),
        "description": resolve_text_with_effects(item.get("desc"), effects),
        "raw_description": item.get("desc"),
        "icon": to_cdn_url(item.get("icon")),
        "associated_traits": [trait_lookup.get(t, t) for t in item.get("associatedTraits", [])],
        "incompatible_traits": [trait_lookup.get(t, t) for t in item.get("incompatibleTraits", [])],
        "effects": effects,
    }


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def write_units_csv(path: Path, units: list) -> None:
    fields = [
        "name", "apiName", "cost", "traits", "health", "mana", "initial_mana",
        "armor", "magic_resist", "attack_damage", "attack_speed",
        "crit_chance", "range", "ability_name", "ability_description",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for u in units:
            s = u["stats"]
            writer.writerow({
                "name": u["name"],
                "apiName": u["apiName"],
                "cost": u["cost"],
                "traits": "; ".join(u["traits"]),
                "health": s["health"],
                "mana": s["mana"],
                "initial_mana": s["initial_mana"],
                "armor": s["armor"],
                "magic_resist": s["magic_resist"],
                "attack_damage": s["attack_damage"],
                "attack_speed": s["attack_speed"],
                "crit_chance": s["crit_chance"],
                "range": s["range"],
                "ability_name": u["ability"]["name"],
                "ability_description": (u["ability"]["description"] or "").replace("\n", " "),
            })


def write_traits_csv(path: Path, traits: list) -> None:
    fields = ["name", "apiName", "description", "breakpoints"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in traits:
            breakpoints = "; ".join(
                f"{e['min_units']}+" for e in t["effects"] if e.get("min_units") is not None
            )
            writer.writerow({
                "name": t["name"],
                "apiName": t["apiName"],
                "description": (t["description"] or "").replace("\n", " "),
                "breakpoints": breakpoints,
            })


def write_items_csv(path: Path, items: list) -> None:
    fields = ["name", "apiName", "unique", "composition", "associated_traits", "description"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for it in items:
            writer.writerow({
                "name": it["name"],
                "apiName": it["apiName"],
                "unique": it["unique"],
                "composition": "; ".join(c["name"] for c in it["composition"]),
                "associated_traits": "; ".join(it["associated_traits"]),
                "description": (it["description"] or "").replace("\n", " "),
            })


def write_augments_csv(path: Path, augments: list) -> None:
    fields = ["name", "apiName", "associated_traits", "description"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for a in augments:
            writer.writerow({
                "name": a["name"],
                "apiName": a["apiName"],
                "associated_traits": "; ".join(a["associated_traits"]),
                "description": (a["description"] or "").replace("\n", " "),
            })


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(set_number: int, output_dir: str, refresh: bool) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "cdragon_tft_raw.json"

    data = load_cdragon_data(cache_path, refresh)

    champs, raw_traits = find_set_champions_and_traits(data, set_number)
    if not champs:
        print(
            f"No champions found for TFT Set {set_number}. The set may not exist, "
            f"or Community Dragon may not have finished indexing it yet.",
            file=sys.stderr,
        )
        sys.exit(1)

    trait_lookup = build_global_trait_lookup(data)
    item_lookup = build_global_item_lookup(data)
    raw_equipment, raw_augments = find_items_and_augments(data, set_number)

    # Drop non-purchasable helper entries that occasionally leak into the
    # champion list (summons, cut content) -- real units always have a cost.
    playable = [c for c in champs if c.get("cost")]

    units = sorted((build_unit_record(c) for c in playable), key=lambda u: (u["cost"], u["name"] or ""))
    trait_records = sorted((build_trait_record(t) for t in raw_traits), key=lambda t: t["name"] or "")
    item_records = sorted(
        (build_item_record(i, item_lookup, trait_lookup) for i in raw_equipment),
        key=lambda i: i["name"] or "",
    )
    augment_records = sorted(
        (build_augment_record(a, trait_lookup) for a in raw_augments),
        key=lambda a: a["name"] or "",
    )

    write_json(out_dir / f"set{set_number}_units.json", units)
    write_units_csv(out_dir / f"set{set_number}_units.csv", units)

    write_json(out_dir / f"set{set_number}_traits.json", trait_records)
    write_traits_csv(out_dir / f"set{set_number}_traits.csv", trait_records)

    write_json(out_dir / f"set{set_number}_items.json", item_records)
    write_items_csv(out_dir / f"set{set_number}_items.csv", item_records)

    write_json(out_dir / f"set{set_number}_augments.json", augment_records)
    write_augments_csv(out_dir / f"set{set_number}_augments.csv", augment_records)

    print(f"\nExtracted for TFT Set {set_number}:")
    print(f"  {len(units)} playable units")
    by_cost = {}
    for u in units:
        by_cost[u["cost"]] = by_cost.get(u["cost"], 0) + 1
    for cost in sorted(by_cost):
        print(f"    {cost}-cost: {by_cost[cost]} units")
    print(f"  {len(trait_records)} traits")
    print(f"  {len(item_records)} items (components + completed + artifacts/emblems)")
    print(f"  {len(augment_records)} augments")

    print(f"\nFiles written to {out_dir.resolve()}:")
    for kind in ("units", "traits", "items", "augments"):
        print(f"  set{set_number}_{kind}.json")
        print(f"  set{set_number}_{kind}.csv")


def main():
    parser = argparse.ArgumentParser(
        description="Datamine TFT unit, trait, item, and augment data from Community Dragon."
    )
    parser.add_argument(
        "--set", type=int, default=18,
        help="TFT set number to extract (default: 18, Enchanted Wilds)",
    )
    parser.add_argument(
        "--output-dir", default="tft_data",
        help="Directory to write output files to (default: ./tft_data)",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Force re-download instead of using the local cache",
    )
    args = parser.parse_args()
    run(args.set, args.output_dir, args.refresh)


if __name__ == "__main__":
    main()
