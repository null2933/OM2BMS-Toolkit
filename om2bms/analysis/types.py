from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SongInfo:
    title: str
    subtitle: str
    artist: str
    subartist: str
    song_last_ms: float
    total: float
    total_notes: int
    md5: Optional[str] = None
    sha256: Optional[str] = None


@dataclass
class ParsedBMSChart:
    timeline_master: list[list[float]]
    song_info: SongInfo


@dataclass
class DifficultyEstimate:
    raw_score: float
    estimated_difficulty: float
    table: str
    label: str
    sub_label: str
    display: str
    source: str
    runtime_provider: Optional[str] = None
