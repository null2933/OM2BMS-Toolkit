# OSZ to BMS Difficulty-Analysis Integration

## Layering

The integration keeps conversion as the main flow and adds difficulty estimation as a post-processing step:

1. `om2bms/pipeline/osz_reader.py`
   Reads and extracts `.osz` archives and copies support assets.
2. Existing osu parser / converter core
   `om2bms/osu.py` and `om2bms/om_to_bms.py`
3. `om2bms/pipeline/conversion.py`
   Wraps one converted output chart into a stable `ConvertedChart` record.
4. `om2bms/analysis/bms_parser.py`
   Parses final exported BMS text/files into timeline data.
5. `om2bms/analysis/feature_extractor.py`
   Reuses the upstream Python analytical core from `pyodide-analyzer.js` and prepares `[1, 600, 46]` ONNX input.
6. `om2bms/analysis/onnx_runtime.py`
   Loads 25 ONNX models and performs ensemble inference.
7. `om2bms/analysis/service.py`
   Public chart-analysis service supporting file path and in-memory text/object input.
8. `om2bms/pipeline/service.py`
   Orchestrates conversion plus post-analysis with `off / single / all`.
9. `om2bms/services/*.py`
   Thin service-layer entry points used by CLI/GUI.

## Key Data Contracts

`ConversionOptions`:

- `enable_difficulty_analysis`
- `difficulty_analysis_mode`
- `difficulty_target_id`
- plus existing conversion knobs (`hitsound`, `bg`, `offset`, `judge`)

`DifficultyAnalysisMode`:

- `off`
- `single`
- `all`

`ConvertedChart`:

- Stable `chart_id`
- `chart_index`
- Source `.osu` identity
- Output file path/name
- Conversion status/error

`AnalysisResult`:

- `chart_id`
- `enabled`
- `status = skipped | success | failed`
- `estimated_difficulty`
- `raw_score`
- Display labels
- Per-chart error

`ConversionResult`:

- `conversion_success`
- `charts`
- `analysis_results`
- `conversion_error`
- `analysis_error`

## Single-Target Resolution

Single-target analysis is not tied to fixed difficulty names.

The selector can match:

- `chart_id`
- `output_file_name`
- `source_chart_name`
- `source_osu_path`
- `difficulty_label`
- `chart_index`

If the selector is ambiguous, analysis is not run and `analysis_error` is populated.

## Runtime Assets

Model assets live under:

- `om2bms/analysis_assets/estimate/oof_dict_ensemble.json`
- `om2bms/analysis_assets/estimate/run*_fold*_single.onnx`
- `om2bms/analysis_assets/estimate/pyodide-analyzer.js`

These are collected into the EXE build by `build_exe.ps1`.
