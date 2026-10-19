"""
tft_board_eval
==============

Analyzes a TFT player's board and assesses how strong it is.

Two scoring paths, by design:

  1. A rule-based heuristic (heuristic.py) that works right now, with
     zero training data -- it's the thing to use today.
  2. A trainable ML model (model.py) that learns from labeled examples
     of real games (board state -> final placement). It expects data
     matching the schema documented in schema.py. This repo doesn't
     include a VOD-scraping pipeline -- that's intentionally left as a
     future piece -- but everything here is built around that data
     contract so wiring one in later is just "point the trainer at a
     real dataset" with no changes to this package.

See README.md for usage and the full data contract.
"""
from .schema import Board, BoardUnit, TrainingExample
from .reference_data import ReferenceData
from .heuristic import score_board, HeuristicResult
from .model import BoardStrengthModel

__all__ = [
    "Board", "BoardUnit", "TrainingExample", "ReferenceData",
    "score_board", "HeuristicResult", "BoardStrengthModel",
]
