from __future__ import annotations

import re

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable
import shutil
import tempfile

from om2bms.pipeline.service import ConversionPipelineService
from om2bms.pipeline.types import ConversionOptions, DifficultyAnalysisMode

from om2bms.analysis.bms_parser import (
    calculate_md5,
    calculate_sha256,
    parse_chart_path,
)


SUPPORTED_BMS_EXTENSIONS = {".bms", ".bme", ".bml", ".pms"}

LogFunc = Callable[[str], None] | None


class UnsupportedBmsChartError(ValueError):
    pass


def _log(log_func: LogFunc, message: str) -> None:
    if log_func is not None:
        log_func(message)


def is_supported_bms_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_BMS_EXTENSIONS


def ensure_supported_bms_path(path: str | Path) -> Path:
    chart_path = Path(path)

    if not chart_path.exists():
        raise FileNotFoundError(f"BMS chart file not found: {chart_path}")

    if not chart_path.is_file():
        raise FileNotFoundError(f"BMS chart path is not a file: {chart_path}")

    suffix = chart_path.suffix.lower()
    if suffix not in SUPPORTED_BMS_EXTENSIONS:
        raise UnsupportedBmsChartError(
            f"Unsupported BMS chart extension: {suffix}. "
            f"Only {sorted(SUPPORTED_BMS_EXTENSIONS)} are allowed."
        )

    return chart_path


def read_bms_hashes(chart_path: str | Path) -> dict[str, str]:
    """
    读取 BMS 文件 md5 / sha256。
    """
    path = ensure_supported_bms_path(chart_path)
    data = path.read_bytes()

    return {
        "md5": calculate_md5(data).lower(),
        "sha256": calculate_sha256(data).lower(),
    }


def parse_bms_summary(chart_path: str | Path) -> dict[str, Any]:
    """
    解析 BMS 基础信息。

    不做难度分析，只读取：
    - song_info
    - timeline_rows
    """
    path = ensure_supported_bms_path(chart_path)
    parsed_chart = parse_chart_path(path)

    return {
        "song_info": asdict(parsed_chart.song_info),
        "timeline_rows": len(parsed_chart.timeline_master),
    }


_OSU_URL_RE = re.compile(
    r"^\s*;?\s*OSU_URL\s*:\s*(?P<url>\S+)\s*$",
    re.IGNORECASE,
)


def read_osu_url_from_bms(chart_path: str | Path) -> str:
    """
    从 BMS 文件中读取类似：

        ; OSU_URL: https://osu.ppy.sh/beatmapsets/...

    的注释。
    """
    path = Path(chart_path)

    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                match = _OSU_URL_RE.match(line)
                if match:
                    return match.group("url").strip()
    except OSError:
        return ""

    return ""


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """
    安全地把 dataclass 对象转成 dict。

    ConversionResult / ConvertedChart / AnalysisResult 当前是 dataclass，
    但这里做得稍微宽松一点，方便后续兼容。
    """
    if obj is None:
        return {}

    if is_dataclass(obj):
        return asdict(obj)

    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)

    return {}


def build_analysis_index_by_chart_id(result: Any) -> dict[str, Any]:
    """
    按 chart_id 建立 BMS 分析结果索引。
    """
    index: dict[str, Any] = {}

    for analysis in getattr(result, "analysis_results", None) or []:
        chart_id = getattr(analysis, "chart_id", None)
        if chart_id is None:
            continue

        index[str(chart_id)] = analysis

    return index


def read_bms_extra_info(
    chart_path: str | Path,
    *,
    log_func: LogFunc = None,
) -> dict[str, Any]:
    """
    从 BMS 文件读取合并 JSON 需要的额外信息。

    包含：
    - bms_hashes
    - bms_summary
    - osu_url

    不包含难度分析，避免重复调用 DifficultyAnalyzerService。
    """
    path = Path(chart_path)

    payload: dict[str, Any] = {
        "bms_hashes": None,
        "bms_summary": None,
        "osu_url": "",
    }

    if not path.exists():
        payload["bms_extra_error"] = f"BMS file not found: {path}"
        return payload

    if not path.is_file():
        payload["bms_extra_error"] = f"BMS path is not a file: {path}"
        return payload

    if not is_supported_bms_path(path):
        payload["bms_extra_error"] = f"Unsupported BMS extension: {path.suffix}"
        return payload

    try:
        payload["bms_hashes"] = read_bms_hashes(path)
    except Exception as exc:
        payload["bms_hashes_error"] = str(exc)
        _log(log_func, f"[BMS JSON] 读取 BMS hash 失败: {exc}")

    try:
        payload["bms_summary"] = parse_bms_summary(path)
    except Exception as exc:
        payload["bms_summary_error"] = str(exc)
        _log(log_func, f"[BMS JSON] 解析 BMS summary 失败: {exc}")

    try:
        payload["osu_url"] = read_osu_url_from_bms(path)
    except Exception as exc:
        payload["osu_url_error"] = str(exc)
        _log(log_func, f"[BMS JSON] 读取 OSU_URL 失败: {exc}")

    return payload


def build_bms_chart_payload(
    chart: Any,
    analysis: Any | None = None,
    *,
    include_extra_info: bool = True,
    log_func: LogFunc = None,
) -> dict[str, Any]:
    """
    把一个 ConvertedChart 和对应 AnalysisResult 合并成 JSON chart 对象。
    """
    chart_payload = dataclass_to_dict(chart)

    if analysis is not None:
        chart_payload["analysis"] = dataclass_to_dict(analysis)
    else:
        chart_payload["analysis"] = None

    if not include_extra_info:
        return chart_payload

    output_path = getattr(chart, "output_path", None)

    if not output_path:
        chart_payload["bms_hashes"] = None
        chart_payload["bms_summary"] = None
        chart_payload["osu_url"] = ""
        return chart_payload

    extra_info = read_bms_extra_info(
        output_path,
        log_func=log_func,
    )

    chart_payload.update(extra_info)

    return chart_payload


def build_bms_result_payload(
    result: Any,
    *,
    analyze_bms: bool,
    output_bms: bool,
    temporary_output: bool,
    include_extra_info: bool = True,
    log_func: LogFunc = None,
) -> dict[str, Any]:
    """
    把 ConversionPipelineService.convert_osu_file() 返回的 ConversionResult
    转成最终合并 JSON 中的 bms 字段。

    注意：
    如果 output_bms=False，会使用临时目录。
    这个函数必须在临时目录删除前调用，否则无法读取 BMS 文件 hash / summary。
    """
    analysis_by_chart_id = build_analysis_index_by_chart_id(result)

    charts_payload: list[dict[str, Any]] = []

    for chart in getattr(result, "charts", None) or []:
        chart_id = getattr(chart, "chart_id", None)

        analysis = None
        if chart_id is not None:
            analysis = analysis_by_chart_id.get(str(chart_id))

        chart_payload = build_bms_chart_payload(
            chart,
            analysis,
            include_extra_info=include_extra_info,
            log_func=log_func,
        )

        charts_payload.append(chart_payload)

    return {
        "enabled": True,
        "converted": bool(getattr(result, "conversion_success", False)),
        "analyzed": bool(analyze_bms),
        "output_bms": bool(output_bms),
        "temporary_output": bool(temporary_output),
        "output_directory": getattr(result, "output_directory", None),
        "conversion_error": getattr(result, "conversion_error", None),
        "analysis_error": getattr(result, "analysis_error", None),
        "charts": charts_payload,
    }


def build_empty_bms_payload(
    *,
    output_bms: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    没有触发 BMS 转换/分析时，写入最终 JSON 的空 bms 字段。
    """
    return {
        "enabled": False,
        "converted": False,
        "analyzed": False,
        "output_bms": bool(output_bms),
        "temporary_output": False,
        "output_directory": None,
        "conversion_error": None,
        "analysis_error": None,
        "skip_reason": reason,
        "charts": [],
    }


def build_bms_error_payload(
    exc: Exception | str,
    *,
    analyze_bms: bool,
    output_bms: bool,
    temporary_output: bool,
) -> dict[str, Any]:
    """
    BMS 转换/分析异常时，写入最终 JSON 的 bms 字段。
    """
    return {
        "enabled": True,
        "converted": False,
        "analyzed": bool(analyze_bms),
        "output_bms": bool(output_bms),
        "temporary_output": bool(temporary_output),
        "output_directory": None,
        "conversion_error": str(exc),
        "analysis_error": None,
        "charts": [],
    }


def should_run_bms_after_mixed(
    data: dict[str, Any] | None,
    *,
    enable_bms_analysis: bool,
    output_bms: bool,
    get_route_mode_func: Callable[[dict[str, Any] | None], str | None],
) -> tuple[bool, bool, str]:
    """
    根据 mixed 分析结果决定是否执行 BMS 转换/难度分析。

    规则：
        1. output_bms=True 时，始终转换并输出 BMS。
        2. enable_bms_analysis=True 时，始终转换 BMS，用于构建基本 BMS 信息。
        3. 只有 enable_bms_analysis=True 且 route.mode == "RC" 时，才执行 BMS 难度分析。
        4. enable_bms_analysis=False 且 output_bms=False 时，完全跳过 BMS。
    """

    route_mode = get_route_mode_func(data)

    # 是否执行 BMS 难度分析
    should_analyze_bms = enable_bms_analysis and route_mode == "RC"

    # 是否执行 BMS 转换
    #
    # 新逻辑：
    # 只要启用了 BMS 分析，就需要转换 BMS，
    # 即使 route.mode 不是 RC，也要通过临时转换收集基本 BMS 信息。
    should_convert_bms = output_bms or enable_bms_analysis

    if not should_convert_bms:
        reason = "BMS 分析未开启，且 BMS 输出未开启"
        return False, False, reason

    if output_bms and should_analyze_bms:
        reason = 'output_bms=True，始终转换输出；且 route.mode == "RC"，执行 BMS 难度分析'

    elif output_bms and not should_analyze_bms:
        if enable_bms_analysis:
            reason = (
                f"output_bms=True，始终转换输出；"
                f"route.mode={route_mode!r}，不执行 BMS 难度分析，仅构建基本 BMS 信息"
            )
        else:
            reason = (
                f"output_bms=True，始终转换输出；"
                f"BMS 分析未开启，不执行 BMS 难度分析"
            )

    elif not output_bms and should_analyze_bms:
        reason = 'route.mode == "RC"，使用临时目录转换 BMS，并执行 BMS 难度分析'

    else:
        reason = (
            f"BMS 分析已开启，使用临时目录转换 BMS；"
            f"但 route.mode={route_mode!r}，不执行 BMS 难度分析，仅构建基本 BMS 信息"
        )

    return True, should_analyze_bms, reason



def build_bms_conversion_options(
    osu_file: Path,
    analyze_bms: bool,
) -> ConversionOptions:
    """
    构建 BMS 转换参数。
    """

    return ConversionOptions(
        hitsound=True,
        bg=True,
        offset=0,
        tn_value=0.2,
        judge=3,
        output_folder_name=osu_file.stem,
        enable_difficulty_analysis=bool(analyze_bms),
        difficulty_analysis_mode=(
            DifficultyAnalysisMode.ALL
            if analyze_bms
            else DifficultyAnalysisMode.OFF
        ),
        difficulty_target_id=None,
        include_output_content=False,
    )


def run_bms_convert_and_analysis(
    *,
    osu_file: Path,
    analyze_bms: bool,
    output_bms: bool,
    output_dir: Path,
    bms_output_dir: Path | None,
    log_func: LogFunc,
) -> tuple[bool, dict[str, Any]]:
    """
    执行 BMS 转换和分析。

    返回:
        success: 是否转换成功
        bms_payload: BMS payload
    """

    bms_temp_dir: Path | None = None

    try:
        if output_bms:
            real_output_dir = bms_output_dir or output_dir
            temporary_output = False
            log_func("[BMS] 输出 BMS: 是")
            log_func(f"[BMS] 输出目录: {real_output_dir}")
        else:
            bms_temp_dir = Path(tempfile.mkdtemp(prefix="mixed_bms_"))
            real_output_dir = bms_temp_dir
            temporary_output = True
            log_func("[BMS] 输出 BMS: 否，使用临时目录")
            log_func(f"[BMS] 临时目录: {real_output_dir}")

        log_func(f"[BMS] BMS 难度分析: {'是' if analyze_bms else '否'}")

        options = build_bms_conversion_options(
            osu_file=osu_file,
            analyze_bms=analyze_bms,
        )

        service = ConversionPipelineService()

        result = service.convert_osu_file(
            osu_path=osu_file,
            output_dir=real_output_dir,
            options=options,
        )

        log_bms_conversion_result(
            result=result,
            analyze_bms=analyze_bms,
            log_func=log_func,
        )

        # 在清理临时目录前构建 BMS payload，因为可能需要读取文件 hash
        bms_payload = build_bms_result_payload(
            result,
            analyze_bms=analyze_bms,
            output_bms=output_bms,
            temporary_output=temporary_output,
            include_extra_info=True,
            log_func=log_func,
        )

        success = bool(result.conversion_success)

        return success, bms_payload

    except Exception as exc:
        log_func(f"[BMS ERROR] {exc}")

        bms_payload = build_bms_error_payload(
            exc,
            analyze_bms=analyze_bms,
            output_bms=output_bms,
            temporary_output=not output_bms,
        )

        return False, bms_payload

    finally:
        if bms_temp_dir is not None and not output_bms:
            try:
                shutil.rmtree(bms_temp_dir, ignore_errors=True)
                log_func(f"[BMS] 已清理临时目录: {bms_temp_dir}")
            except Exception as exc:
                log_func(f"[BMS] 清理临时目录失败: {exc}")


def log_bms_conversion_result(
    *,
    result: Any,
    analyze_bms: bool,
    log_func: LogFunc,
) -> None:
    """
    打印 BMS 转换和分析结果。
    """

    log_func("")
    log_func("[BMS] convert 服务完成")
    log_func(f"[BMS] 转换成功: {'是' if result.conversion_success else '否'}")

    if getattr(result, "output_directory", None):
        log_func(f"[BMS] 输出目录: {result.output_directory}")

    if getattr(result, "conversion_error", None):
        log_func(f"[BMS] 转换错误: {result.conversion_error}")

    if getattr(result, "analysis_error", None):
        log_func(f"[BMS] 分析警告: {result.analysis_error}")

    charts = getattr(result, "charts", []) or []
    analysis_results = getattr(result, "analysis_results", []) or []

    if charts:
        log_func("[BMS] 转换谱面:")

        for chart in charts:
            chart_id = getattr(chart, "chart_id", "")
            name = getattr(chart, "source_chart_name", "")
            status = getattr(chart, "conversion_status", "")
            output_path = getattr(chart, "output_path", None)
            error = getattr(chart, "conversion_error", None)

            log_func(f"[BMS] - {chart_id} | {name} | {status}")

            if output_path:
                log_func(f"[BMS]   输出: {output_path}")

            if error:
                log_func(f"[BMS]   错误: {error}")

    if not analyze_bms:
        log_func("[BMS] 本次仅转换，不执行 BMS 难度分析")
        return

    if analysis_results:
        log_func("[BMS] 分析结果:")

        for item in analysis_results:
            chart_id = getattr(item, "chart_id", "")
            status = getattr(item, "status", "")
            enabled = getattr(item, "enabled", False)

            if status == "success":
                display = getattr(item, "difficulty_display", None)
                estimated = getattr(item, "estimated_difficulty", None)
                raw_score = getattr(item, "raw_score", None)
                table = getattr(item, "difficulty_table", None)
                label = getattr(item, "difficulty_label", None)
                source = getattr(item, "analysis_source", None)
                provider = getattr(item, "runtime_provider", None)

                value = display or estimated or "未知"

                log_func(f"[BMS] - {chart_id}: success")
                log_func(f"[BMS]   难度: {value}")

                if raw_score is not None:
                    log_func(f"[BMS]   rawScore: {raw_score}")

                if table or label:
                    log_func(f"[BMS]   表: {table or ''} {label or ''}".rstrip())

                if source:
                    log_func(f"[BMS]   source: {source}")

                if provider:
                    log_func(f"[BMS]   runtime: {provider}")

            elif status == "failed":
                error = getattr(item, "error", None)
                log_func(f"[BMS] - {chart_id}: failed")

                if error:
                    log_func(f"[BMS]   错误: {error}")

            elif status == "skipped":
                log_func(
                    f"[BMS] - {chart_id}: skipped"
                    f"{'，enabled=False' if not enabled else ''}"
                )

            else:
                log_func(f"[BMS] - {chart_id}: {status}")
    else:
        log_func("[BMS] 没有分析结果")