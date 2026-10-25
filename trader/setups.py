"""Shared types for all setup detectors (chart patterns and the levels strategy)."""
from dataclasses import dataclass, field

UP, DOWN = "UP", "DOWN"


@dataclass
class Setup:
    pattern: str
    direction: str
    b: int            # breakout candle
    level: float      # line the breakout closed beyond
    stop: float
    height: float     # pattern height (measured move)
    key: tuple        # identifies this pattern instance
    start: int        # first candle of the pattern
    points: dict = field(default_factory=dict)  # key points, for charts
    zone_target: float = None   # levels setups: edge of the next opposing zone (exit E3)
