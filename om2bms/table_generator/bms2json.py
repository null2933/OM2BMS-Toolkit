from __future__ import annotations

import json
import re

from dataclasses import asdict
from pathlib import Path
from typing import Any

from om2bms.analysis.bms_parser import calculate_md5, calculate_sha256, parse_chart_path
from om2bms.table_generator.score_parser import ParsedScore, ScoreEntry, parse_score_path
from om2bms.analysis.service import DifficultyAnalyzerService

SUPPORTED_EXTENSIONS = {".bms", ".bme", ".bml", ".pms"}


class UnsupportedChartError(ValueError):
    pass


class ScoreJsonFormatError(ValueError):
    pass


def is_supported_bms_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def ensure_supported_bms_path(path: str | Path) -> Path:
    chart_path = Path(path)

    if not chart_path.exists():
        raise FileNotFoundError(f"Chart file not found: {chart_path}")

    if not chart_path.is_file():
        raise FileNotFoundError(f"Chart path is not a file: {chart_path}")

    if chart_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedChartError(
            f"Unsupported chart file extension: {chart_path.suffix}. "
            f"Only {sorted(SUPPORTED_EXTENSIONS)} are allowed."
        )

    return chart_path


def ensure_score_json_path(path: str | Path) -> Path:
    score_path = Path(path)

    if not score_path.exists():
        raise FileNotFoundError(f"Score JSON file not found: {score_path}")

    if not score_path.is_file():
        raise FileNotFoundError(f"Score JSON path is not a file: {score_path}")

    return score_path


def build_score_index_by_md5(scores: ParsedScore) -> dict[str, ScoreEntry]:
    index: dict[str, ScoreEntry] = {}

    for entry in scores.entries:
        entry_md5 = entry.md5.strip().lower()
        if not entry_md5:
            continue
        index[entry_md5] = entry

    return index


def verify_sha256(entry: ScoreEntry, bms_sha256: str) -> bool | None:
    json_sha256 = (entry.sha256 or "").strip().lower()
    target_sha256 = (bms_sha256 or "").strip().lower()

    if not json_sha256:
        return None

    return json_sha256 == target_sha256



def read_bms_hashes(chart_path: str | Path) -> dict[str, str]:
    chart_path = ensure_supported_bms_path(chart_path)
    data = chart_path.read_bytes()

    return {
        "md5": calculate_md5(data).lower(),
        "sha256": calculate_sha256(data).lower(),
    }


def parse_bms_summary(chart_path: str | Path) -> dict[str, Any]:
    chart_path = ensure_supported_bms_path(chart_path)
    parsed_chart = parse_chart_path(chart_path)

    return {
        "song_info": asdict(parsed_chart.song_info),
        "timeline_rows": len(parsed_chart.timeline_master),
    }


def match_bms_to_score(
    bms_path: str | Path,
    score_json_path: str | Path,
) -> dict[str, Any]:
    chart_path = ensure_supported_bms_path(bms_path)
    score_path = ensure_score_json_path(score_json_path)

    hashes = read_bms_hashes(chart_path)
    summary = parse_bms_summary(chart_path)

    parsed_scores = parse_score_path(score_path)
    score_index = build_score_index_by_md5(parsed_scores)
    matched_score = score_index.get(hashes["md5"])

    if matched_score is None:
        return {
            "matched": False,
            "reason": "md5_not_found",
            "sha256_verified": False,
            "bms_path": str(chart_path),
            "score_json_path": str(score_path),
            "bms_md5": hashes["md5"],
            "bms_sha256": hashes["sha256"],
            **summary,
        }

    sha256_verified = verify_sha256(matched_score, hashes["sha256"])
    if sha256_verified is True:
        reason = "ok"
    elif sha256_verified is False:
        reason = "sha256_mismatch"
    else:
        reason = "sha256_skipped"

    return {
        "matched": True,
        "reason": reason,
        "sha256_verified": sha256_verified,
        "bms_path": str(chart_path),
        "score_json_path": str(score_path),
        "bms_md5": hashes["md5"],
        "bms_sha256": hashes["sha256"],
        "score_entry": asdict(matched_score),
        **summary,
    }


from pathlib import Path
from typing import Any


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _analyze_level_with_service(chart_path: Path, song_info: object) -> dict[str, str]:
    ln_ratio_threshold = 0.15  # 判断是否为 LN_Type 依据

    try:
        if isinstance(song_info, dict):
            ln_ratio = float(song_info.get("ln_ratio", song_info.get("ln_ration", 0.0)) or 0.0)
        else:
            ln_ratio = float(getattr(song_info, "ln_ratio", 0.0) or 0.0)

        if ln_ratio > ln_ratio_threshold:
            return {
                "level": "LN",
                "comment": "LN",
            }

        service = DifficultyAnalyzerService()
        result = service.analyze_path(str(chart_path))
        if result is None:
            print("Analyze Failed")
            return {
                "level": "",
                "comment": "",
            }

        return {
            "level": _safe_str(result.label, ""),
            "comment": _safe_str(result.display, ""),
        }

    except Exception as e:
        print(f"Analyze Failed: {e}")
        return {
            "level": "",
            "comment": "",
        }



_OSU_URL_RE = re.compile(
    r"^\s*;?\s*OSU_URL\s*:\s*(?P<url>\S+)\s*$",
    re.IGNORECASE,
)


def read_osu_url_from_bms(bms_path: str | Path) -> str:
    path = Path(bms_path)

    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                m = _OSU_URL_RE.match(line)
                if m:
                    return m.group("url").strip()
    except OSError:
        return ""

    return ""

def build_score_entry_from_bms(
    bms_path: str | Path,
    *,
    use_custom_level: bool = False,
    custom_level: str | int | None = None,
) -> dict[str, Any]:
    chart_path = ensure_supported_bms_path(bms_path)

    hashes = read_bms_hashes(chart_path)
    parsed_chart = parse_chart_path(chart_path)
    song_info = parsed_chart.song_info

    title = (song_info.title or "").strip()
    subtitle = (song_info.subtitle or "").strip()

    merged_title = title
    if subtitle:
        merged_title = f"{title} {subtitle}".strip()

    total_notes = song_info.total_notes or 0
    total_value = song_info.total or 0
    judge = song_info.judge or 3

    try:
        total_notes = int(total_notes)
    except (TypeError, ValueError):
        total_notes = 0

    try:
        total_value = int(float(total_value))
    except (TypeError, ValueError):
        total_value = 0

    if use_custom_level:
        level = str(custom_level or "").strip()
        comment = ""
    else:
        analyzed = _analyze_level_with_service(chart_path,song_info)
        level = analyzed["level"]
        comment = analyzed["comment"]

    osu_url = read_osu_url_from_bms(chart_path)
    artist = song_info.artist.strip() if song_info.artist else ""
    subartist = song_info.subartist.strip() if song_info.subartist else ""

    merged_artist = "/".join(x for x in [artist, subartist] if x)


    return {
        "title": merged_title,
        "level": level,
        "eval": 0,
        "artist": (merged_artist or "").strip(),
        "url": osu_url,
        "url_diff": "",
        "name_diff": "",
        "comment": comment,
        "note": total_notes,
        "total": total_value,
        "judge": judge,
        "md5": hashes["md5"],
        "sha256": hashes["sha256"],
    }



def match_or_build_missing_entry(
    bms_path: str | Path,
    score_json_path: str | Path,
    *,
    use_custom_level: bool = False,
    custom_level: str = "",
) -> dict[str, Any]:
    result = match_bms_to_score(bms_path, score_json_path)

    if result["matched"]:
        return result

    generated_entry = build_score_entry_from_bms(
        bms_path,
        use_custom_level=use_custom_level,
        custom_level=custom_level,
    )

    return {
        **result,
        "generated_score_entry": generated_entry,
    }



def load_score_json_root(score_json_path: str | Path) -> list[dict[str, Any]]:
    score_path = Path(score_json_path)

    if not score_path.exists():
        return []

    text = score_path.read_text(encoding="utf-8")
    stripped = text.strip()

    if not stripped:
        return []

    data = json.loads(stripped)

    if not isinstance(data, list):
        raise ScoreJsonFormatError("score.json root must be a JSON array.")

    normalized: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            normalized.append(item)

    return normalized


def has_md5_in_score_json(score_json_path: str | Path, md5_value: str) -> bool:
    md5_value = md5_value.strip().lower()
    items = load_score_json_root(score_json_path)

    for item in items:
        item_md5 = str(item.get("md5", "")).strip().lower()
        if item_md5 == md5_value:
            return True

    return False


def append_score_entry_to_json(
    score_json_path: str | Path,
    entry: dict[str, Any],
    *,
    skip_if_md5_exists: bool = True,
) -> bool:
    score_path = Path(score_json_path)
    items = load_score_json_root(score_path) if score_path.exists() else []

    entry_md5 = str(entry.get("md5", "")).strip().lower()
    if not entry_md5:
        raise ValueError("Entry md5 is empty.")

    if skip_if_md5_exists:
        for item in items:
            item_md5 = str(item.get("md5", "")).strip().lower()
            if item_md5 == entry_md5:
                return False

    items.append(entry)
    score_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def emit_missing_entry_json(
    bms_path: str | Path,
    score_json_path: str | Path,
) -> str | None:
    result = match_or_build_missing_entry(bms_path, score_json_path)
    generated_entry = result.get("generated_score_entry")

    if generated_entry is None:
        return None

    return json.dumps(generated_entry, ensure_ascii=False, indent=2)


def append_missing_entry_if_needed(
    bms_path: str | Path,
    score_json_path: str | Path,
    *,
    use_custom_level: bool = False,
    custom_level: str = "",
) -> dict[str, Any]:
    result = match_or_build_missing_entry(
        bms_path,
        score_json_path,
        use_custom_level=use_custom_level,
        custom_level=custom_level,
    )

    if result["matched"]:
        return {
            **result,
            "appended": False,
        }

    generated_entry = result["generated_score_entry"]
    appended = append_score_entry_to_json(
        score_json_path,
        generated_entry,
        skip_if_md5_exists=True,
    )

    return {
        **result,
        "appended": appended,
    }



def print_match_result_human(result: dict[str, Any]) -> None:
    print("Match Result")
    print("============")
    print(f"matched         : {result.get('matched')}")
    print(f"reason          : {result.get('reason')}")
    print(f"sha256_verified : {result.get('sha256_verified')}")
    print(f"bms_path        : {result.get('bms_path')}")
    print(f"score_json_path : {result.get('score_json_path')}")
    print(f"bms_md5         : {result.get('bms_md5')}")
    print(f"bms_sha256      : {result.get('bms_sha256')}")
    print()

    song_info = result.get("song_info") or {}
    print("BMS Info")
    print("========")
    print(f"title           : {song_info.get('title')}")
    print(f"subtitle        : {song_info.get('subtitle')}")
    print(f"artist          : {song_info.get('artist')}")
    print(f"subartist       : {song_info.get('subartist')}")
    print(f"total           : {song_info.get('total')}")
    print(f"total_notes     : {song_info.get('total_notes')}")
    print(f"song_last_ms    : {song_info.get('song_last_ms')}")
    print(f"timeline_rows   : {result.get('timeline_rows')}")
    print()

    score_entry = result.get("score_entry")
    if score_entry is not None:
        print("Matched Score Entry")
        print("===================")
        for key, value in score_entry.items():
            print(f"{key:<15}: {value}")
        print()

    generated_entry = result.get("generated_score_entry")
    if generated_entry is not None:
        print("Generated Score Entry")
        print("=====================")
        print(json.dumps(generated_entry, ensure_ascii=False, indent=2))
        print()
