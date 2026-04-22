from __future__ import annotations

from pathlib import Path

from om2bms.analysis.service import DifficultyAnalyzerService
from om2bms.analysis.types import DifficultyEstimate


class DifficultyAnalysisService:
    def __init__(self, analyzer: DifficultyAnalyzerService | None = None) -> None:
        self.analyzer = analyzer or DifficultyAnalyzerService()

    def analyze_file(self, chart_path: str | Path) -> DifficultyEstimate:
        return self.analyzer.analyze_path(chart_path)

    def analyze_text(self, chart_text: str, chart_id: str | None = None) -> DifficultyEstimate:
        return self.analyzer.analyze_text(chart_text, chart_id=chart_id)

