from __future__ import annotations

from copy import deepcopy
from typing import Any
from om2bms.result_processor.pattern_processor import build_pattern_fields


def get_by_path(data: Any, path: str, default: Any = None) -> Any:
    """
    支持 dict 和 list 的 dot path 取值。

    Example:
        bms.charts.0.bms_summary.song_info.title
    """

    current: Any = data

    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
            continue

        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return default

            if index < 0 or index >= len(current):
                return default

            current = current[index]
            continue

        return default

    return current



def first_not_none(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value

    return default


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def round_number(value: Any, digits: int = 2) -> float | None:
    number = to_float(value)

    if number is None:
        return None

    return round(number, digits)


def to_int(value: Any) -> int | None:
    number = to_float(value)

    if number is None:
        return None

    return int(round(number))

def format_percent(value: Any, digits: int = 2) -> str | None:
    number = to_float(value)

    if number is None:
        return None

    percent = round(number * 100, digits)

    if percent.is_integer():
        return f"{int(percent)}%"

    return f"{percent}%"


def format_ms_to_min_sec(value: Any) -> str | None:
    """
    把毫秒转换成 分钟:秒。

    Example:
        189964.528 -> "3:10"
    """
    number = to_float(value)

    if number is None:
        return None

    total_seconds = int(round(number / 1000))
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    return f"{minutes}:{seconds:02d}"


def merge_title_subtitle(title: Any, subtitle: Any) -> str | None:
    """
    合并 title 和 subtitle。

    Example:
        title = "Anata Ga Mawaru"
        subtitle = "[Extreme]"
        -> "Anata Ga Mawaru [Extreme]"
    """
    if title is None and subtitle is None:
        return None

    title_text = str(title).strip() if title is not None else ""
    subtitle_text = str(subtitle).strip() if subtitle is not None else ""

    if title_text and subtitle_text:
        return f"{title_text} {subtitle_text}"

    if title_text:
        return title_text

    if subtitle_text:
        return subtitle_text

    return None



def normalize_route_type(route_mode: Any) -> str | None:
    """
    把 route.mode 转成最终输出用的 type。
    """
    if not isinstance(route_mode, str):
        return None

    mode = route_mode.upper()

    mapping = {
        "RC": "RC",
        "LN": "LN",
        "HB": "HB",
        "MIX": "MIX",
    }

    return mapping.get(mode, mode)


def build_dan_estimate(data: dict[str, Any]) -> str | None:
    """
    dan_estimate 只取 compact.estDiff。
    """

    value = get_by_path(data, "compact.estDiff")

    if value is None:
        return None

    return str(value)


def build_derived_fields(data: dict[str, Any]) -> dict[str, Any]:
    """
    从 raw_data 中构建最终输出前需要的派生字段。
    """

    title = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.title",
    )

    subtitle = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.subtitle",
    )

    artist = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.artist",
    )

    keys = get_by_path(
        data,
        "compact.columnCount",
    )

    route_mode = get_by_path(
        data,
        "route.mode",
    )

    sunny_sr = get_by_path(
        data,
        "compact.star",
    )

    ln_ratio = get_by_path(
        data,
        "compact.lnRatio",
    )

    total = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.total",
    )

    song_last_ms = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.song_last_ms",
    )

    bms_difficulty_table = get_by_path(
        data,
        "bms.charts.0.analysis.difficulty_table",
    )

    bms_difficulty_label = get_by_path(
        data,
        "bms.charts.0.analysis.difficulty_label",
    )

    bms_difficulty_display = get_by_path(
        data,
        "bms.charts.0.analysis.difficulty_display",
    )

    osu_url = get_by_path(
        data,
        "bms.charts.0.osu_url",
    )

    md5 = get_by_path(
        data,
        "bms.charts.0.bms_hashes.md5",
    )

    sha256 = get_by_path(
        data,
        "bms.charts.0.bms_hashes.sha256",
    )

    total_notes = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.total_notes",
    )

    judge = get_by_path(
        data,
        "bms.charts.0.bms_summary.song_info.judge",
    )

    derived = {
        # title + subtitle 合并
        "title": merge_title_subtitle(title, subtitle),

        # 如果最终不想输出 subtitle，config 里不要映射它
        "subtitle": subtitle,

        "artist": artist,
        "keys": keys,
        "type": normalize_route_type(route_mode),

        # star/sunny_sr 保留两位
        "star": round_number(sunny_sr, 2),
        "sunny_sr": round_number(sunny_sr, 2),

        "dan_estimate": build_dan_estimate(data),

        "bms_difficulty_table": bms_difficulty_table,
        "bms_difficulty_label": bms_difficulty_label,
        "bms_difficulty_display": bms_difficulty_display,

        "osu_url": osu_url,
        "md5": md5,
        "sha256": sha256,

        "total_notes": total_notes,

        # song_last_ms 转换成 分钟:秒
        "song_length": format_ms_to_min_sec(song_last_ms),

        # 如果你还想保留原始毫秒，可以保留这个
        "song_last_ms": song_last_ms,

        "ln_ratio": format_percent(ln_ratio, 2),

        # total 保留整数
        "total": to_int(total),

        "judge": judge,
    }
    pattern_fields = build_pattern_fields(data)
    derived.update(pattern_fields)
    
    return derived



def remove_none_values(data: dict[str, Any]) -> dict[str, Any]:
    """
    删除值为 None 的字段。
    """
    return {
        key: value
        for key, value in data.items()
        if value is not None
    }


def prepare_final_result_source(
    raw_data: dict[str, Any],
    *,
    remove_none: bool = False,
) -> dict[str, Any]:
    """
    最终 JSON 映射前的预处理入口。

    输入:
        raw_data

    输出:
        带 derived 字段的新 dict

    注意:
        不直接修改传入的 raw_data。
    """
    processed = deepcopy(raw_data)

    derived = build_derived_fields(processed)

    if remove_none:
        derived = remove_none_values(derived)

    processed["derived"] = derived

    return processed
