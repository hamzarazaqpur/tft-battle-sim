"""
Board / unit data model, JSON (de)serialization, and -- importantly --
the data contract that future VOD-derived training examples must
follow. This file is the hand-off point for a scraping pipeline built
later: match this shape and nothing else in the package needs to change.

----------------------------------------------------------------------
BOARD INPUT SCHEMA  (what you hand the analyzer to score one board)
----------------------------------------------------------------------
{
  "stage": "4-2",                     # optional, "<round>-<turn>"
  "units": [
    {"name": "<champion name>", "star": 2, "items": ["<item name>", "<item name>"]},
    ...
  ]
}

Unit/item names are matched case-insensitively against the set
reference data (ReferenceData), i.e. whatever tft_set18_scraper.py
produced for the set you're analyzing.

----------------------------------------------------------------------
TRAINING EXAMPLE SCHEMA  (what a future VOD-mining pipeline should emit)
----------------------------------------------------------------------
One JSON object per labeled example -- either one per line in a
.jsonl file, or one file per example in a directory. See model.py's
load_examples_jsonl() / load_examples_dir().

{
  "board": { ... same shape as the Board input schema above ... },
  "context": {
    "stage": "4-2",
    "patch": "18.1",
    "players_remaining": 6
  },
  "outcome": {
    "final_placement": 3          # 1 (1st place) through 8 (last)
  },
  "source": {
    "video_id": "dQw4w9WgXcQ",    # for traceability back to the VOD
    "timestamp_seconds": 1423.0
  }
}

"context" and "source" are optional metadata (kept for traceability
and future stratified analysis, e.g. "does this hold at every stage");
only "board" and "outcome.final_placement" are required to train on.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class BoardUnit:
    name: str
    star: int = 1
    items: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "BoardUnit":
        return cls(
            name=d["name"],
            star=int(d.get("star", 1)),
            items=list(d.get("items", []))[:3],  # a unit can hold at most 3 items
        )

    def to_dict(self) -> dict:
        return {"name": self.name, "star": self.star, "items": self.items}


@dataclass
class Board:
    units: List[BoardUnit]
    stage: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        return cls(units=[BoardUnit.from_dict(u) for u in d.get("units", [])], stage=d.get("stage"))

    @classmethod
    def from_json_file(cls, path) -> "Board":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        d = {"units": [u.to_dict() for u in self.units]}
        if self.stage:
            d["stage"] = self.stage
        return d


@dataclass
class TrainingExample:
    board: Board
    final_placement: int
    stage: Optional[str] = None
    patch: Optional[str] = None
    players_remaining: Optional[int] = None
    video_id: Optional[str] = None
    timestamp_seconds: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "TrainingExample":
        board = Board.from_dict(d["board"])
        outcome = d.get("outcome", {})
        context = d.get("context", {})
        source = d.get("source", {})
        if "final_placement" not in outcome:
            raise ValueError("Training example is missing outcome.final_placement")
        return cls(
            board=board,
            final_placement=int(outcome["final_placement"]),
            stage=context.get("stage"),
            patch=context.get("patch"),
            players_remaining=context.get("players_remaining"),
            video_id=source.get("video_id"),
            timestamp_seconds=source.get("timestamp_seconds"),
        )

    @property
    def strength_label(self) -> float:
        """
        Map placement 1..8 onto a 0..100 'strength' scale, matching the
        heuristic scorer's output range so the two can be compared or
        blended later (e.g. 1st place -> 100, 8th place -> 0).
        """
        placement = max(1, min(8, self.final_placement))
        return (9 - placement) / 8.0 * 100.0


def unknown_names(board: Board, ref) -> List[str]:
    """Champion/item names on the board that aren't in the reference
    data -- usually a typo or a name from the wrong set."""
    problems = []
    for bu in board.units:
        if ref.unit(bu.name) is None:
            problems.append(f"unit: {bu.name!r}")
        for item_name in bu.items:
            if ref.item(item_name) is None:
                problems.append(f"item: {item_name!r} (on {bu.name})")
    return problems
