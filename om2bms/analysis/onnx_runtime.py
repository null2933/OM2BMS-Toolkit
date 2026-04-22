from __future__ import annotations

import json
import os

from pathlib import Path

import numpy as np
import onnxruntime as ort

from om2bms.analysis.assets import DifficultyAnalysisAssets


DEFAULT_PROVIDER_PRIORITY = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
)
PROVIDER_ENV_VAR = "OM2BMS_ORT_PROVIDER"
PROVIDER_ALIASES = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "gpu": "CUDAExecutionProvider",
    "dml": "DmlExecutionProvider",
    "directml": "DmlExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
}


def calculate_iqr_mean(predictions: list[float], min_iqr: float = 0.02) -> float:
    if not predictions:
        return 0.0
    if len(predictions) <= 2:
        return sum(predictions) / len(predictions)

    sorted_values = sorted(float(value) for value in predictions)

    def percentile(values: list[float], p: float) -> float:
        pos = (len(values) - 1) * p
        base = int(pos)
        rest = pos - base
        if base + 1 < len(values):
            return values[base] + rest * (values[base + 1] - values[base])
        return values[base]

    q1 = percentile(sorted_values, 0.25)
    q3 = percentile(sorted_values, 0.75)
    iqr = max(q3 - q1, min_iqr)

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    inliers = [value for value in predictions if lower_bound <= value <= upper_bound]
    if inliers:
        return float(sum(inliers) / len(inliers))

    return float(sorted_values[len(sorted_values) // 2])


class OnnxDifficultyModelEnsemble:
    def __init__(self, assets: DifficultyAnalysisAssets | None = None) -> None:
        self.assets = assets or DifficultyAnalysisAssets.discover()
        self.sessions: list[ort.InferenceSession] = []
        self.oof_dict: dict[str, dict[str, float]] = {}
        self.available_providers: list[str] = []
        self.session_providers: list[str] = []
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return

        self.assets.validate_runtime_files()
        self.available_providers = list(ort.get_available_providers())
        self.session_providers = self._resolve_session_providers(self.available_providers)
        with self.assets.oof_dict_path.open("r", encoding="utf-8") as handle:
            self.oof_dict = json.load(handle)

        for model_path in self.assets.model_paths:
            session = ort.InferenceSession(
                str(model_path),
                providers=self.session_providers,
            )
            self.sessions.append(session)
        self._loaded = True

    @property
    def runtime_provider(self) -> str | None:
        if not self.sessions:
            return self.session_providers[0] if self.session_providers else None
        return self.sessions[0].get_providers()[0]

    def describe_runtime(self) -> str:
        primary = self.runtime_provider or "unknown"
        available = ", ".join(self.available_providers) if self.available_providers else "unknown"
        configured = ", ".join(self.session_providers) if self.session_providers else "unknown"
        return f"active={primary}; configured=[{configured}]; available=[{available}]"

    def _resolve_session_providers(self, available_providers: list[str]) -> list[str]:
        requested = os.environ.get(PROVIDER_ENV_VAR, "auto").strip().lower()

        if not available_providers:
            return ["CPUExecutionProvider"]

        if requested and requested != "auto":
            requested_provider = PROVIDER_ALIASES.get(requested, requested)
            provider_order: list[str] = []
            if requested_provider in available_providers:
                provider_order.append(requested_provider)
            if "CPUExecutionProvider" in available_providers and requested_provider != "CPUExecutionProvider":
                provider_order.append("CPUExecutionProvider")
            if provider_order:
                return provider_order

        ordered = [provider for provider in DEFAULT_PROVIDER_PRIORITY if provider in available_providers]
        if ordered:
            return ordered

        return list(available_providers)

    def try_cache_lookup(self, md5_hash: str) -> float | None:
        self.load()
        value = self.oof_dict.get(md5_hash)
        if not value or value.get("label") == 0.0:
            return None
        return float(value["pred"])

    def predict(self, input_x: np.ndarray, tns_value: float) -> float:
        self.load()

        if not self.sessions:
            raise RuntimeError("No ONNX sessions are available.")

        tns_tensor = np.array([[tns_value]], dtype=np.float32)
        predictions: list[float] = []
        for session in self.sessions:
            output_name = session.get_outputs()[0].name
            feeds: dict[str, np.ndarray] = {}
            for input_meta in session.get_inputs():
                input_name = input_meta.name
                input_shape = tuple(input_meta.shape)
                if input_name.lower() == "x" or len(input_shape) == 3:
                    feeds[input_name] = input_x
                else:
                    feeds[input_name] = tns_tensor

            result = session.run([output_name], feeds)
            predictions.append(float(result[0].reshape(-1)[0]))

        return calculate_iqr_mean(predictions)
