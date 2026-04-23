from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from om2bms.analysis.types import ParsedBMSChart, SongInfo


MAIN_DATA_PATTERN = re.compile(r"^#(?P<measure>\d{3})(?P<channel>[0-9A-Z]{2}):(?P<data>.+)$", re.IGNORECASE)
BPM_EXT_PATTERN = re.compile(r"^#BPM(?P<key>[0-9A-Z]{2})\s+(?P<value>.+)$", re.IGNORECASE)
HEADER_PATTERN = re.compile(r"^#(?P<key>[A-Z][A-Z0-9_]*)\s+(?P<value>.+)$", re.IGNORECASE)
LN_RATIO_PATTERN = re.compile(r"^;\s*(?:LN_RATIO)\s*:\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s*$",re.IGNORECASE,)


@dataclass(frozen=True)
class _TimedEvent:
    event_type: str
    measure: int
    fraction: float
    lane: int | None = None
    bpm: float | None = None


def calculate_md5(data: bytes) -> str:
    #print("md5:"+hashlib.md5(data).hexdigest())
    return hashlib.md5(data).hexdigest()


def calculate_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def smart_decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be")

    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_chart_path(chart_path: str | Path) -> ParsedBMSChart:
    path = Path(chart_path)
    return parse_chart_bytes(path.read_bytes())


def parse_chart_text(chart_text: str) -> ParsedBMSChart:
    return _parse_bms_text(chart_text)


def parse_chart_bytes(data: bytes) -> ParsedBMSChart:
    stripped = data.lstrip()
    if stripped.startswith(b"{"):
        return _parse_chart_object(json.loads(smart_decode(data)))
    return _parse_bms_text(smart_decode(data))


def parse_chart_object(chart_object: dict[str, Any] | ParsedBMSChart) -> ParsedBMSChart:
    if isinstance(chart_object, ParsedBMSChart):
        return chart_object
    return _parse_chart_object(chart_object)


def _parse_chart_object(chart_object: dict[str, Any]) -> ParsedBMSChart:
    if "timeline_master" in chart_object and "song_info" in chart_object:
        info = chart_object["song_info"]
        song_info = SongInfo(
            title=str(info.get("title", "")),
            subtitle=str(info.get("subtitle", "")),
            artist=str(info.get("artist", "")),
            subartist=str(info.get("subartist", "")),
            song_last_ms=float(info.get("song_last_ms", 0.0)),
            total=float(info.get("total", 200.0)),
            total_notes=int(info.get("total_notes", 0)),
            judge = int(info.get("judge", 3)),
            ln_ratio=_safe_float(
                info.get("ln_ratio", info.get("ln_ration", 0.0)),
                default=0.0,
            ),
            md5=info.get("md5"),
            sha256=info.get("sha256"),
        )

        return ParsedBMSChart(
            timeline_master=[[float(value) for value in row] for row in chart_object["timeline_master"]],
            song_info=song_info,
        )
    raise TypeError("Unsupported chart object. Expected ParsedBMSChart or dict with timeline_master/song_info.")


def _parse_bms_text(text: str) -> ParsedBMSChart:
    headers: dict[str, str] = {}
    extended_bpms: dict[str, float] = {}
    measure_lengths: dict[int, float] = {}
    timed_events: list[_TimedEvent] = []
    ln_ratio = 0.0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue

        
        ln_ratio_match = LN_RATIO_PATTERN.match(line)
        if ln_ratio_match:
            ln_ratio = _safe_float(ln_ratio_match.group("value"), default=0.0)
            continue


        main_match = MAIN_DATA_PATTERN.match(line)
        if main_match:
            measure = int(main_match.group("measure"))
            channel = main_match.group("channel").upper()
            data = main_match.group("data").strip()

            if channel == "02":
                try:
                    measure_lengths[measure] = float(data)
                except ValueError:
                    pass
                continue

            tokens = _split_pairs(data)
            if not tokens:
                continue

            for index, token in enumerate(tokens):
                if token == "00":
                    continue
                fraction = index / len(tokens)
                lane = _lane_index_from_channel(channel)
                if lane is not None:
                    timed_events.append(_TimedEvent("note", measure, fraction, lane=lane))
                    continue

                if channel == "03":
                    timed_events.append(_TimedEvent("bpm", measure, fraction, bpm=float(int(token, 16))))
                elif channel == "08":
                    bpm_value = extended_bpms.get(token.upper())
                    if bpm_value is not None:
                        timed_events.append(_TimedEvent("bpm", measure, fraction, bpm=bpm_value))
            continue

        bpm_ext_match = BPM_EXT_PATTERN.match(line)
        if bpm_ext_match:
            try:
                extended_bpms[bpm_ext_match.group("key").upper()] = float(bpm_ext_match.group("value").strip())
            except ValueError:
                pass
            continue

        header_match = HEADER_PATTERN.match(line)
        if header_match:
            key = header_match.group("key").lower()
            headers[key] = header_match.group("value").strip()

    base_bpm = _safe_float(headers.get("bpm"), default=120.0)
    extracted_notes = _resolve_note_times(timed_events, measure_lengths, base_bpm)
    timeline_master = _build_timeline(extracted_notes)

    song_info = SongInfo(
        title=headers.get("title", ""),
        subtitle=headers.get("subtitle", ""),
        artist=headers.get("artist", ""),
        subartist=headers.get("subartist", ""),
        song_last_ms=extracted_notes[-1][0] if extracted_notes else 0.0,
        total=_safe_float(headers.get("total"), default=200.0),
        total_notes=len(extracted_notes),
        judge = int(headers.get("rank", 3)),
        ln_ratio=ln_ratio,
    )
    return ParsedBMSChart(timeline_master=timeline_master, song_info=song_info)


def _split_pairs(data: str) -> list[str]:
    clean = data.strip()
    if len(clean) < 2:
        return []
    pair_count = len(clean) // 2
    return [clean[index * 2:index * 2 + 2] for index in range(pair_count)]


def _lane_index_from_channel(channel: str) -> int | None:
    try:
        value = int(channel, 10)
    except ValueError:
        return None

    base = value - 40 if 50 <= value <= 59 else value
    mapping = {
        11: 0,
        12: 1,
        13: 2,
        14: 3,
        15: 4,
        18: 5,
        19: 6,
        16: 7,
    }
    return mapping.get(base)


def _resolve_note_times(
    timed_events: list[_TimedEvent],
    measure_lengths: dict[int, float],
    base_bpm: float,
) -> list[tuple[float, int]]:
    if not timed_events:
        return []

    events_by_measure: dict[int, list[_TimedEvent]] = {}
    max_measure = 0
    for event in timed_events:
        events_by_measure.setdefault(event.measure, []).append(event)
        max_measure = max(max_measure, event.measure)
    if measure_lengths:
        max_measure = max(max_measure, max(measure_lengths))

    current_bpm = base_bpm
    measure_start_ms = 0.0
    extracted_notes: list[tuple[float, int]] = []

    for measure in range(max_measure + 1):
        measure_events = events_by_measure.get(measure, [])
        measure_ratio = measure_lengths.get(measure, 1.0)
        measure_beats = 4.0 * measure_ratio
        grouped = _group_events_by_fraction(measure_events)

        current_ms = measure_start_ms
        previous_fraction = 0.0

        for fraction in sorted(grouped):
            delta_fraction = fraction - previous_fraction
            current_ms += _beats_to_ms(delta_fraction * measure_beats, current_bpm)

            note_events = [event for event in grouped[fraction] if event.event_type == "note"]
            for event in note_events:
                extracted_notes.append((current_ms, int(event.lane)))

            bpm_events = [event for event in grouped[fraction] if event.event_type == "bpm" and event.bpm]
            for event in bpm_events:
                current_bpm = float(event.bpm)

            previous_fraction = fraction

        current_ms += _beats_to_ms((1.0 - previous_fraction) * measure_beats, current_bpm)
        measure_start_ms = current_ms

    extracted_notes.sort(key=lambda item: item[0])
    return extracted_notes


def _group_events_by_fraction(events: list[_TimedEvent]) -> dict[float, list[_TimedEvent]]:
    grouped: dict[float, list[_TimedEvent]] = {}
    for event in events:
        key = round(event.fraction, 10)
        grouped.setdefault(key, []).append(event)
    return grouped


def _build_timeline(extracted_notes: list[tuple[float, int]]) -> list[list[float]]:
    timeline: list[list[float]] = []
    current_time: float | None = None
    current_row: list[float] | None = None

    for time_ms, lane in extracted_notes:
        grouped_time = round(time_ms, 6)
        if current_time != grouped_time:
            if current_row is not None:
                timeline.append(current_row)
            current_time = grouped_time
            current_row = [grouped_time] + [0.0] * 8
        assert current_row is not None
        current_row[lane + 1] = 1.0

    if current_row is not None:
        timeline.append(current_row)
    return timeline


def _beats_to_ms(beats: float, bpm: float) -> float:
    if bpm <= 0:
        return 0.0
    return beats * 60000.0 / bpm


def _safe_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default
