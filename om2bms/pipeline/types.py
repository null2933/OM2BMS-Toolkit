from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional


class DifficultyAnalysisMode(str, Enum):
    OFF = "off"
    SINGLE = "single"
    ALL = "all"

    @classmethod
    def from_value(cls, value: str | "DifficultyAnalysisMode" | None) -> "DifficultyAnalysisMode":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.OFF
        return cls(str(value).lower())


@dataclass
class ConversionOptions:
    hitsound: bool = True
    bg: bool = True
    offset: int = 0
    tn_value: float = 0.2
    judge: int = 3
    output_folder_name: Optional[str] = None
    enable_difficulty_analysis: bool = False
    difficulty_analysis_mode: DifficultyAnalysisMode | str | None = None
    difficulty_target_id: Optional[str] = None
    include_output_content: bool = False

    def resolved_analysis_mode(self) -> DifficultyAnalysisMode:
        if not self.enable_difficulty_analysis:
            return DifficultyAnalysisMode.OFF

        if self.difficulty_analysis_mode is None:
            if self.difficulty_target_id:
                return DifficultyAnalysisMode.SINGLE
            return DifficultyAnalysisMode.ALL

        mode = DifficultyAnalysisMode.from_value(self.difficulty_analysis_mode)
        if mode == DifficultyAnalysisMode.OFF:
            return DifficultyAnalysisMode.OFF
        if mode == DifficultyAnalysisMode.SINGLE and not self.difficulty_target_id:
            raise ValueError("difficulty_target_id is required when difficulty_analysis_mode is 'single'.")
        return mode


@dataclass
class ConvertedChart:
    chart_id: str
    chart_index: int
    source_chart_name: str
    source_osu_path: str
    difficulty_label: Optional[str]
    output_path: Optional[str]
    output_file_name: Optional[str]
    output_content: Optional[str] = None
    conversion_status: str = "success"
    conversion_error: Optional[str] = None

    def selector_candidates(self) -> Iterable[str]:
        values = [
            self.chart_id,
            str(self.chart_index),
            self.source_chart_name,
            self.source_osu_path,
            self.output_file_name,
            self.difficulty_label,
        ]
        seen: set[str] = set()
        for value in values:
            if value is None:
                continue
            normalized = str(value).strip()
            if not normalized:
                continue
            lowered = normalized.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            yield normalized

    def matches_selector(self, selector: str) -> bool:
        selector_normalized = selector.strip().casefold()
        return any(candidate.casefold() == selector_normalized for candidate in self.selector_candidates())

    @property
    def output_path_obj(self) -> Optional[Path]:
        return Path(self.output_path) if self.output_path else None


@dataclass
class AnalysisResult:
    chart_id: str
    enabled: bool
    status: str
    estimated_difficulty: Optional[float]
    raw_score: Optional[float]
    difficulty_table: Optional[str]
    difficulty_label: Optional[str]
    difficulty_display: Optional[str]
    analysis_source: Optional[str]
    runtime_provider: Optional[str]
    error: Optional[str]
    output_path: Optional[str]


@dataclass
class ConversionResult:
    conversion_success: bool
    charts: list[ConvertedChart] = field(default_factory=list)
    analysis_results: list[AnalysisResult] = field(default_factory=list)
    output_directory: Optional[str] = None
    conversion_error: Optional[str] = None
    analysis_error: Optional[str] = None

    def analysis_result_for(self, chart_id: str) -> Optional[AnalysisResult]:
        for result in self.analysis_results:
            if result.chart_id == chart_id:
                return result
        return None
