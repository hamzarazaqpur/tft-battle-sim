"""
Loads the TFT set reference data that tft_set18_scraper.py produces
(set{N}_units.json, set{N}_traits.json, set{N}_items.json) and exposes
case-insensitive lookup tables used by the rest of the pipeline.

This is the only place that cares about the on-disk file layout from
the scraper -- everything downstream works off ReferenceData objects.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional


class ReferenceData:
    def __init__(self, units: List[dict], traits: List[dict], items: List[dict], augments: Optional[List[dict]] = None):
        self.units_by_name: Dict[str, dict] = {u["name"].lower(): u for u in units if u.get("name")}
        self.traits_by_name: Dict[str, dict] = {t["name"].lower(): t for t in traits if t.get("name")}
        self.items_by_name: Dict[str, dict] = {i["name"].lower(): i for i in items if i.get("name")}
        self.augments_by_name: Dict[str, dict] = {a["name"].lower(): a for a in (augments or []) if a.get("name")}

    def unit(self, name: str) -> Optional[dict]:
        return self.units_by_name.get((name or "").strip().lower())

    def trait(self, name: str) -> Optional[dict]:
        return self.traits_by_name.get((name or "").strip().lower())

    def item(self, name: str) -> Optional[dict]:
        return self.items_by_name.get((name or "").strip().lower())

    def augment(self, name: str) -> Optional[dict]:
        return self.augments_by_name.get((name or "").strip().lower())

    @classmethod
    def load(cls, data_dir, set_number: int = 18) -> "ReferenceData":
        """
        Load from the output directory of tft_set18_scraper.py. Expects
        set{N}_units.json, set{N}_traits.json, set{N}_items.json; the
        augments file is optional (older scraper runs may not have one).
        """
        data_dir = Path(data_dir)

        def _load(name: str, required: bool = True):
            p = data_dir / f"set{set_number}_{name}.json"
            if not p.exists():
                if required:
                    raise FileNotFoundError(
                        f"Expected {p} -- run tft_set18_scraper.py first "
                        f"(or point --data-dir at its output directory)."
                    )
                return []
            return json.loads(p.read_text(encoding="utf-8"))

        units = _load("units")
        traits = _load("traits")
        items = _load("items")
        augments = _load("augments", required=False)
        return cls(units, traits, items, augments)
