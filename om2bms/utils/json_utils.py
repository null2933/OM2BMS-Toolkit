from __future__ import annotations

import json

from pathlib import Path
from typing import Any


def extract_json_from_stdout(stdout_text: str) -> dict[str, Any] | None:
    """
    从 Node stdout 中提取 mixed JSON。

    兼容：
    1. stdout 本身就是 JSON
    2. stdout 前后带日志，中间包含 JSON
    """
    text = stdout_text.strip()

    if not text:
        return None

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start < 0 or end < 0 or end <= start:
        return None

    candidate = text[start : end + 1]

    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def get_route_mode(data: dict[str, Any] | None) -> str | None:
    """
    获取 mixed JSON 中的 route.mode。
    """
    if not isinstance(data, dict):
        return None

    route = data.get("route")

    if isinstance(route, dict):
        mode = route.get("mode")
        return str(mode) if mode is not None else None

    flat_mode = data.get("route.mode")
    return str(flat_mode) if flat_mode is not None else None


def remove_summary_text(data: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    copied = dict(data)
    summary_text = copied.pop("summaryText", None)

    if summary_text is not None:
        summary_text = str(summary_text)

    return copied, summary_text


def build_default_bms_payload(
    *,
    output_bms: bool = False,
) -> dict[str, Any]:
    return {
        "enabled": False,
        "converted": False,
        "analyzed": False,
        "output_bms": bool(output_bms),
        "temporary_output": False,
        "output_directory": None,
        "conversion_error": None,
        "analysis_error": None,
        "charts": [],
    }


def build_merged_json_data(
    mixed_data: dict[str, Any],
    *,
    osu_file: str | Path,
    exit_code: int,
    runner_file: str | Path,
    bms_payload: dict[str, Any] | None = None,
    output_bms: bool = False,
) -> dict[str, Any]:
    json_data, _summary_text = remove_summary_text(mixed_data)

    if bms_payload is None:
        bms_payload = build_default_bms_payload(output_bms=output_bms)

    json_data["bms"] = bms_payload

    json_data["_gui"] = {
        "sourceOsu": str(osu_file),
        "exitCode": exit_code,
        "runner": str(runner_file),
    }

    return json_data


def save_json_file(
    data: dict[str, Any],
    json_path: str | Path,
) -> Path:
    path = Path(json_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return path


def save_merged_json_file(
    mixed_data: dict[str, Any],
    json_path: str | Path,
    *,
    osu_file: str | Path,
    exit_code: int,
    runner_file: str | Path,
    bms_payload: dict[str, Any] | None = None,
    output_bms: bool = False,
) -> Path:
    merged_data = build_merged_json_data(
        mixed_data,
        osu_file=osu_file,
        exit_code=exit_code,
        runner_file=runner_file,
        bms_payload=bms_payload,
        output_bms=output_bms,
    )

    return save_json_file(merged_data, json_path)


def make_unique_json_path(
    output_dir: str | Path,
    osu_file: str | Path,
    *,
    suffix: str = ".mixed.json",
) -> Path:
    out_dir = Path(output_dir)
    osu_path = Path(osu_file)

    out_dir.mkdir(parents=True, exist_ok=True)

    base_name = osu_path.stem
    candidate = out_dir / f"{base_name}{suffix}"

    if not candidate.exists():
        return candidate

    if suffix.endswith(".json"):
        suffix_body = suffix[:-5]
    else:
        suffix_body = suffix

    index = 1

    while True:
        candidate = out_dir / f"{base_name}{suffix_body}.{index}.json"
        if not candidate.exists():
            return candidate

        index += 1
