from __future__ import annotations

from pathlib import Path

from om2bms.analysis.service import DifficultyAnalyzerService
from om2bms.pipeline.conversion import convert_single_osu_chart, resize_backgrounds
from om2bms.pipeline.osz_reader import copy_support_assets, extracted_osz, list_osu_files
from om2bms.pipeline.types import (
    AnalysisResult,
    ConversionOptions,
    ConversionResult,
    ConvertedChart,
    DifficultyAnalysisMode,
)


class ConversionPipelineService:
    def __init__(self, analyzer: DifficultyAnalyzerService | None = None) -> None:
        self.analyzer = analyzer or DifficultyAnalyzerService()

    def convert_osz_archive(
        self,
        archive_path: str | Path,
        output_dir: str | Path,
        options: ConversionOptions,
    ) -> ConversionResult:
        archive = Path(archive_path)
        output_folder_name = options.output_folder_name or archive.stem
        output_root = Path(output_dir) / output_folder_name
        output_root.mkdir(parents=True, exist_ok=True)

        try:
            with extracted_osz(archive) as extract_dir:
                osu_files = list_osu_files(extract_dir)
                if not osu_files:
                    return ConversionResult(
                        conversion_success=False,
                        charts=[],
                        analysis_results=[],
                        output_directory=str(output_root),
                        conversion_error=f"No .osu files were found in {archive}.",
                    )

                charts: list[ConvertedChart] = []
                bg_files = []

                for chart_index, osu_file in enumerate(osu_files, start=1):
                    try:
                        chart, bg_file = convert_single_osu_chart(
                            osu_file=osu_file,
                            output_dir=output_root,
                            options=options,
                            chart_index=chart_index,
                            extract_root=extract_dir,
                        )
                    except Exception as exc:
                        relative_path = str(Path(osu_file).relative_to(extract_dir))
                        chart = ConvertedChart(
                            chart_id=f"failed-{chart_index}",
                            chart_index=chart_index,
                            source_chart_name=Path(osu_file).name,
                            source_osu_path=relative_path,
                            difficulty_label=None,
                            output_path=None,
                            output_file_name=None,
                            conversion_status="failed",
                            conversion_error=str(exc),
                        )
                        bg_file = None

                    charts.append(chart)
                    if bg_file is not None:
                        bg_files.append(bg_file)

                if options.bg:
                    resize_backgrounds(bg_files)
                copy_support_assets(extract_dir, output_root)

            analysis_results, analysis_error = self._run_analysis(charts, options)
            conversion_success = any(chart.conversion_status == "success" for chart in charts)
            return ConversionResult(
                conversion_success=conversion_success,
                charts=charts,
                analysis_results=analysis_results,
                output_directory=str(output_root),
                analysis_error=analysis_error,
            )
        except Exception as exc:
            return ConversionResult(
                conversion_success=False,
                charts=[],
                analysis_results=[],
                output_directory=str(output_root),
                conversion_error=str(exc),
            )

    def _run_analysis(
        self,
        charts: list[ConvertedChart],
        options: ConversionOptions,
    ) -> tuple[list[AnalysisResult], str | None]:
        mode = options.resolved_analysis_mode()
        successful_charts = [
            chart for chart in charts
            if chart.conversion_status == "success" and chart.output_path
        ]

        if mode == DifficultyAnalysisMode.OFF or not successful_charts:
            return [self._build_skipped_result(chart, enabled=False) for chart in charts], None

        selected_chart_ids: set[str]
        analysis_error = None
        if mode == DifficultyAnalysisMode.ALL:
            selected_chart_ids = {chart.chart_id for chart in successful_charts}
        else:
            try:
                target_chart = self._resolve_single_target(successful_charts, options.difficulty_target_id or "")
                selected_chart_ids = {target_chart.chart_id}
            except Exception as exc:
                selected_chart_ids = set()
                analysis_error = str(exc)

        results: list[AnalysisResult] = []
        for chart in charts:
            if chart.chart_id not in selected_chart_ids:
                results.append(self._build_skipped_result(chart, enabled=False))
                continue

            try:
                estimate = self.analyzer.analyze_path(chart.output_path or "")
                results.append(
                    AnalysisResult(
                        chart_id=chart.chart_id,
                        enabled=True,
                        status="success",
                        estimated_difficulty=estimate.estimated_difficulty,
                        raw_score=estimate.raw_score,
                        difficulty_table=estimate.table,
                        difficulty_label=estimate.sub_label,
                        difficulty_display=estimate.display,
                        analysis_source=estimate.source,
                        runtime_provider=estimate.runtime_provider,
                        error=None,
                        output_path=chart.output_path,
                    )
                )
            except Exception as exc:
                results.append(
                    AnalysisResult(
                        chart_id=chart.chart_id,
                        enabled=True,
                        status="failed",
                        estimated_difficulty=None,
                        raw_score=None,
                        difficulty_table=None,
                        difficulty_label=None,
                        difficulty_display=None,
                        analysis_source=None,
                        runtime_provider=None,
                        error=str(exc),
                        output_path=chart.output_path,
                    )
                )
        return results, analysis_error

    def _resolve_single_target(self, charts: list[ConvertedChart], selector: str) -> ConvertedChart:
        normalized = selector.strip()
        if not normalized:
            raise ValueError("A difficulty target is required when analysis mode is 'single'.")

        matches = [chart for chart in charts if chart.matches_selector(normalized)]
        if not matches:
            raise LookupError(f"No converted chart matched the analysis target: {selector}")
        if len(matches) > 1:
            match_ids = ", ".join(chart.chart_id for chart in matches)
            raise ValueError(f"Analysis target '{selector}' is ambiguous. Matching chartIds: {match_ids}")
        return matches[0]

    def _build_skipped_result(self, chart: ConvertedChart, enabled: bool) -> AnalysisResult:
        return AnalysisResult(
            chart_id=chart.chart_id,
            enabled=enabled,
            status="skipped",
            estimated_difficulty=None,
            raw_score=None,
            difficulty_table=None,
            difficulty_label=None,
            difficulty_display=None,
            analysis_source=None,
            runtime_provider=None,
            error=None,
            output_path=chart.output_path,
        )
