from __future__ import annotations

from pathlib import Path

from om2bms.pipeline.service import ConversionPipelineService
from om2bms.pipeline.types import ConversionOptions, ConversionResult


class ConversionService:
    def __init__(self, pipeline: ConversionPipelineService | None = None) -> None:
        self.pipeline = pipeline or ConversionPipelineService()

    def convert_osz(self, archive_path: str | Path, output_dir: str | Path, options: ConversionOptions) -> ConversionResult:
        return self.pipeline.convert_osz_archive(archive_path, output_dir, options)
