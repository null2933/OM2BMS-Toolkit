from __future__ import annotations

import re

from functools import lru_cache

import numpy as np

from om2bms.analysis.assets import DifficultyAnalysisAssets
from om2bms.analysis.types import SongInfo


RUN_PYTHON_MARKER = "runPythonAsync(`"


@lru_cache(maxsize=1)
def _load_upstream_feature_extractor() -> type:
    assets = DifficultyAnalysisAssets.discover()
    assets.validate_runtime_files()

    source = assets.pyodide_source_path.read_text(encoding="utf-8")
    start = source.index(RUN_PYTHON_MARKER) + len(RUN_PYTHON_MARKER)
    end = source.index("`);", start)
    python_code = source[start:end]

    python_code = python_code.replace("s = song_info_dict.to_py()", "s = song_info_dict")
    python_code = python_code.replace(
        "self.timeline_master = np.array(timeline_master.to_py())",
        "self.timeline_master = np.array(timeline_master)",
    )

    namespace: dict[str, object] = {}
    exec(compile(python_code, str(assets.pyodide_source_path), "exec"), namespace, namespace)
    return namespace["BMS"]  # type: ignore[return-value]


class BMSFeatureExtractor:
    def __init__(self, timeline_master: list[list[float]], song_info: SongInfo) -> None:
        bms_class = _load_upstream_feature_extractor()
        self._analyzer = bms_class(timeline_master, song_info.__dict__)

    def get_window_meta(self, start_ms: float, end_ms: float) -> np.ndarray:
        return np.asarray(self._analyzer.get_window_meta(start_ms, end_ms), dtype=np.float32)


def prepare_inference_data(extractor: BMSFeatureExtractor, song_info: SongInfo) -> np.ndarray:
    window_size = 600
    stride = 200
    max_windows = 600
    meta_dim = 46

    temp_metas: list[np.ndarray] = []
    song_last_ms = int(song_info.song_last_ms)
    for start in range(0, song_last_ms, stride):
        temp_metas.append(extractor.get_window_meta(start, start + window_size))

    if not temp_metas:
        raise ValueError("No valid analysis windows could be extracted from the chart.")

    note_counts = [float(meta[0] + meta[1]) for meta in temp_metas]
    valid_indices = [index for index, count in enumerate(note_counts) if count > 0]
    if not valid_indices:
        raise ValueError("No note-containing windows were found for analysis.")

    last_valid_index = valid_indices[-1]
    valid_length = last_valid_index + 1

    start_index = 0
    if valid_length > max_windows:
        start_index = valid_length - max_windows
        max_density_index = max(range(valid_length), key=lambda index: note_counts[index])
        if max_density_index < start_index:
            start_index = max(0, max_density_index - 10)

    final_metas = temp_metas[start_index:min(start_index + max_windows, valid_length)]
    input_buffer = np.zeros((1, max_windows, meta_dim), dtype=np.float32)

    shift = max_windows - len(final_metas)
    for index, meta in enumerate(final_metas):
        input_buffer[0, shift + index, :] = meta[:meta_dim]
    return input_buffer
