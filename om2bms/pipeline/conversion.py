from __future__ import annotations

import hashlib

from pathlib import Path

from om2bms.converter.image_resizer import black_background_thumbnail, save_banner_image
from om2bms.converter.om_to_bms import OsuManiaToBMSParser
from om2bms.pipeline.types import ConversionOptions, ConvertedChart


def build_chart_id(source_osu_path: str, difficulty_label: str | None, chart_index: int) -> str:
    stable_input = f"{chart_index}|{source_osu_path}|{difficulty_label or ''}"
    return hashlib.sha1(stable_input.encode("utf-8")).hexdigest()[:12]


def convert_single_osu_chart(
    osu_file: str | Path,
    output_dir: str | Path,
    options: ConversionOptions,
    chart_index: int,
    extract_root: str | Path,
) -> tuple[ConvertedChart, Path | None]:
    conversion_options = dict(
        getattr(OsuManiaToBMSParser, "_convertion_options", {}) or {})
    conversion_options.update({
        "HITSOUND": options.hitsound,
        "BG": options.bg,
        "OFFSET": options.offset,
        "JUDGE": options.judge,
    })
    OsuManiaToBMSParser._convertion_options = conversion_options

    osu_path = Path(osu_file)
    parser = OsuManiaToBMSParser(str(osu_path), str(output_dir), osu_path.name)
    beatmap = getattr(parser, "beatmap", None)
    difficulty_label = getattr(
        beatmap, "version", None) if beatmap is not None else None
    source_relative_path = str(osu_path.relative_to(Path(extract_root)))
    chart_id = build_chart_id(source_relative_path,
                              difficulty_label, chart_index)

    if parser.failed:
        chart = ConvertedChart(
            chart_id=chart_id,
            chart_index=chart_index,
            source_chart_name=osu_path.name,
            source_osu_path=source_relative_path,
            difficulty_label=difficulty_label,
            output_path=None,
            output_file_name=None,
            conversion_status="failed",
            conversion_error="Beatmap parsing failed.",
        )
        return chart, None

    output_path = getattr(parser, "output_path", None)
    bg_file = Path(parser.get_bg()) if options.bg and parser.get_bg() else None
    chart = ConvertedChart(
        chart_id=chart_id,
        chart_index=chart_index,
        source_chart_name=osu_path.name,
        source_osu_path=source_relative_path,
        difficulty_label=difficulty_label,
        output_path=output_path,
        output_file_name=Path(output_path).name if output_path else None,
    )
    if options.include_output_content and output_path:
        chart.output_content = Path(output_path).read_text(
            encoding="shift_jis", errors="replace")
    return chart, bg_file


def resize_backgrounds(background_paths: list[Path]) -> None:
    seen: set[str] = set()
    for bg_path in background_paths:
        resolved = str(bg_path.resolve())
        if resolved in seen or not bg_path.exists():
            continue
        save_banner_image(str(bg_path))
        black_background_thumbnail(str(bg_path))
        seen.add(resolved)
