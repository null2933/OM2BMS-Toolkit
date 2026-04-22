from __future__ import annotations

import tempfile
import zipfile

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def extracted_osz(archive_path: str | Path) -> Iterator[Path]:
    archive = Path(archive_path)
    with tempfile.TemporaryDirectory(prefix="om2bms_pipeline_") as temp_dir:
        extract_dir = Path(temp_dir)
        with zipfile.ZipFile(archive, "r") as zip_file:
            zip_file.extractall(extract_dir)
        yield extract_dir


def list_osu_files(extract_dir: str | Path) -> list[Path]:
    base_dir = Path(extract_dir)
    return sorted(base_dir.rglob("*.osu"))


def copy_support_assets(extract_dir: str | Path, output_dir: str | Path) -> None:
    extract_root = Path(extract_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    for file_path in extract_root.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() in {".osu", ".zip"}:
            continue
        target_path = destination / file_path.name
        target_path.write_bytes(file_path.read_bytes())

