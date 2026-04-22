from __future__ import annotations

from pathlib import Path
from typing import Any

from om2bms.analysis.bms_parser import (
    calculate_md5,
    calculate_sha256,
    parse_chart_bytes,
    parse_chart_object,
    parse_chart_path,
    parse_chart_text,
)
from om2bms.analysis.difficulty_mapper import BMSDifficultyMapper
from om2bms.analysis.feature_extractor import BMSFeatureExtractor, prepare_inference_data
from om2bms.analysis.onnx_runtime import OnnxDifficultyModelEnsemble
from om2bms.analysis.types import DifficultyEstimate, ParsedBMSChart


class DifficultyAnalyzerService:
    def __init__(self, runtime: OnnxDifficultyModelEnsemble | None = None) -> None:
        self.runtime = runtime or OnnxDifficultyModelEnsemble()
        self.mapper = BMSDifficultyMapper()

    def analyze_path(self, chart_path: str | Path) -> DifficultyEstimate:
        path = Path(chart_path)
        chart_bytes = path.read_bytes()
        parsed_chart = parse_chart_path(path)
        return self._analyze_parsed_chart(parsed_chart, chart_bytes)

    def analyze_text(self, chart_text: str, chart_id: str | None = None) -> DifficultyEstimate:
        chart_bytes = chart_text.encode("shift_jis", errors="replace")
        parsed_chart = parse_chart_text(chart_text)
        return self._analyze_parsed_chart(parsed_chart, chart_bytes, chart_id=chart_id)

    def analyze_object(self, chart_object: dict[str, Any] | ParsedBMSChart, chart_id: str | None = None) -> DifficultyEstimate:
        parsed_chart = parse_chart_object(chart_object)
        return self._analyze_parsed_chart(parsed_chart, None, chart_id=chart_id)

    def _analyze_parsed_chart(
        self,
        parsed_chart: ParsedBMSChart,
        chart_bytes: bytes | None,
        chart_id: str | None = None,
    ) -> DifficultyEstimate:
        if parsed_chart.song_info.total_notes <= 0:
            raise ValueError("The chart contains no analyzable notes.")

        md5_hash = calculate_md5(chart_bytes) if chart_bytes is not None else None
        sha256_hash = calculate_sha256(chart_bytes) if chart_bytes is not None else None
        parsed_chart.song_info.md5 = md5_hash
        parsed_chart.song_info.sha256 = sha256_hash

        if md5_hash:
            cached_score = self.runtime.try_cache_lookup(md5_hash)
            if cached_score is not None:
                mapping = self.mapper.denormalize(cached_score)
                return DifficultyEstimate(
                    raw_score=cached_score,
                    estimated_difficulty=mapping.level,
                    table=mapping.table,
                    label=mapping.label,
                    sub_label=mapping.sub_label,
                    display=mapping.display,
                    source="cache",
                    runtime_provider=self.runtime.runtime_provider,
                )

        extractor = BMSFeatureExtractor(parsed_chart.timeline_master, parsed_chart.song_info)
        input_x = prepare_inference_data(extractor, parsed_chart.song_info)
        total_notes = parsed_chart.song_info.total_notes
        tns_value = parsed_chart.song_info.total / total_notes
        raw_score = self.runtime.predict(input_x, tns_value)

        mapping = self.mapper.denormalize(raw_score)
        return DifficultyEstimate(
            raw_score=raw_score,
            estimated_difficulty=mapping.level,
            table=mapping.table,
            label=mapping.label,
            sub_label=mapping.sub_label,
            display=mapping.display,
            source="onnx",
            runtime_provider=self.runtime.runtime_provider,
        )
