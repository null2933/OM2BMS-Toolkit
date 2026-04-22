from __future__ import annotations

import sys

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DifficultyAnalysisAssets:
    asset_root: Path
    estimate_dir: Path
    oof_dict_path: Path
    pyodide_source_path: Path
    model_paths: tuple[Path, ...]

    @classmethod
    def discover(cls) -> "DifficultyAnalysisAssets":
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            package_root = Path(sys._MEIPASS) / "om2bms"
        else:
            package_root = Path(__file__).resolve().parent.parent

        estimate_dir = package_root / "analysis_assets" / "estimate"
        model_paths = tuple(
            estimate_dir / f"run{run}_fold{fold}_single.onnx"
            for run in range(1, 6)
            for fold in range(1, 6)
        )
        return cls(
            asset_root=package_root / "analysis_assets",
            estimate_dir=estimate_dir,
            oof_dict_path=estimate_dir / "oof_dict_ensemble.json",
            pyodide_source_path=estimate_dir / "pyodide-analyzer.js",
            model_paths=model_paths,
        )

    def validate_runtime_files(self) -> None:
        missing = [path for path in self.model_paths if not path.exists()]
        if not self.oof_dict_path.exists():
            missing.append(self.oof_dict_path)
        if not self.pyodide_source_path.exists():
            missing.append(self.pyodide_source_path)
        if missing:
            missing_str = ", ".join(str(path) for path in missing[:5])
            if len(missing) > 5:
                missing_str += f" ... (+{len(missing) - 5} more)"
            raise FileNotFoundError(f"Missing difficulty-analysis assets: {missing_str}")
