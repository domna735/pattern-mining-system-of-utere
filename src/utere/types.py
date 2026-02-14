from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatternMatch:
    ticker: str
    u_idx: int
    t_idx: int
    e1_idx: int
    r_idx: int | None
    e2_idx: int | None
    status: str  # 'COMPLETED' | 'UTE_incomplete'
