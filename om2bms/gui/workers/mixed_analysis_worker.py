import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import zipfile
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from om2bms.pipeline.service import ConversionPipelineService
from om2bms.pipeline.types import ConversionOptions, DifficultyAnalysisMode
from om2bms.utils import json_utils, bms_utils
from om2bms.result_processor.field_mapper import apply_field_mapping
from om2bms.result_processor.final_result_processor import prepare_final_result_source


class MixedAnalysisWorker:
    def __init__(
        self,
        project_root: Path,
        runner_file: Path,
        input_file: Path | None,
        output_dir: Path,
        final_result_mapping_config_path: Path,
        log_callback=None,
        finish_callback=None,
        enable_bms_analysis: bool = True,
        output_bms: bool = False,
        bms_output_dir: Path | None = None,

        # 批量处理参数
        input_dir: Path | None = None,
        input_info_file: Path | None = None,
        input_mode: str = "file",
        batch_mode: bool = False,
        merge_json_results: bool = True,
        save_individual_json: bool = True,
        quiet_analysis_logs: bool = True,
    ):
        self.project_root = Path(project_root)
        self.runner_file = Path(runner_file)

        self.input_file = Path(input_file) if input_file else None
        self.input_dir = Path(input_dir) if input_dir else None
        self.input_info_file = Path(input_info_file) if input_info_file else None
        self.input_mode = str(input_mode or "file")

        self.output_dir = Path(output_dir)

        self.log_callback = log_callback
        self.finish_callback = finish_callback

        self.process = None
        self.temp_dir = None
        self.running = False
        self.thread = None

        # BMS 分析参数
        self.enable_bms_analysis = bool(enable_bms_analysis)
        self.output_bms = bool(output_bms)
        self.bms_output_dir = Path(bms_output_dir) if bms_output_dir else None

        # 最终数据整理
        self.final_result_mapping_config_path = (
            Path(final_result_mapping_config_path)
            if final_result_mapping_config_path
            else None
        )
        self.enable_final_result_mapping = final_result_mapping_config_path is not None

        # 批量处理参数
        self.batch_mode = bool(batch_mode)
        self.merge_json_results = bool(merge_json_results)
        self.save_individual_json = bool(save_individual_json)
        self.quiet_analysis_logs = bool(quiet_analysis_logs)

        # 内部固定参数
        self.speed_rate = 1.0
        self.cvt_flag = ""
        self.with_graph = False
        self.summary_only = True

        # 结果来源元数据
        self.analyzer_version = "mixed-worker-1.0"
        # 是否启用已分析跳过
        self.enable_analysis_skip = True

        # index 文件名
        self.analysis_index_filename = "analysis_index.json"
        self.merged_json_name = "final_result.json"


    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def start(self):
        if self.running:
            return False

        self.running = True

        self.thread = threading.Thread(
            target=self._run_safe,
            daemon=True,
        )
        self.thread.start()

        return True

    def stop(self):
        self.running = False

        if self.process is not None:
            try:
                self.process.kill()
                self.log("[GUI] 已停止")
            except Exception as exc:
                self.log(f"[GUI] 停止失败: {exc}")

    # ------------------------------------------------------------------
    # common utils
    # ------------------------------------------------------------------

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def file_md5(self, path: Path) -> str:
        h = hashlib.md5()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def file_sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(self):
        if not self.runner_file.exists() or not self.runner_file.is_file():
            raise FileNotFoundError(f"固定 Node runner 不存在:\n{self.runner_file}")

        if self.input_mode == "folder":
            if self.input_dir is None:
                raise FileNotFoundError("批量模式未指定输入文件夹")

            if not self.input_dir.exists() or not self.input_dir.is_dir():
                raise FileNotFoundError(f"输入文件夹不存在:\n{self.input_dir}")

        elif self.input_mode == "info":
            if self.input_info_file is None:
                raise FileNotFoundError("未指定 beatmap_info.json")

            if not self.input_info_file.exists() or not self.input_info_file.is_file():
                raise FileNotFoundError(
                    f"beatmap_info.json 不存在:\n{self.input_info_file}"
                )

            if self.input_info_file.suffix.lower() != ".json":
                raise ValueError("下载器入口必须是 .json 文件")

        else:
            if self.input_file is None:
                raise FileNotFoundError("输入文件不存在")

            if not self.input_file.exists() or not self.input_file.is_file():
                raise FileNotFoundError("输入文件不存在")

            if self.input_file.suffix.lower() not in [".osu", ".osz"]:
                raise ValueError("输入文件必须是 .osu 或 .osz")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not self.output_dir.exists() or not self.output_dir.is_dir():
            raise ValueError("JSON 保存路径必须是目录")

    # ------------------------------------------------------------------
    # main flow
    # ------------------------------------------------------------------

    def get_route_mode(self, data):
        return json_utils.get_route_mode(data)

    def should_run_bms_after_mixed(self, data):
        return bms_utils.should_run_bms_after_mixed(
            data,
            enable_bms_analysis=self.enable_bms_analysis,
            output_bms=self.output_bms,
            get_route_mode_func=self.get_route_mode,
        )
    

    def sanitize_folder_name(self, name: str) -> str:
        """
        清理 Windows / BMS 输出目录不安全字符。
        只清理单级文件夹名，不允许路径穿透。
        """

        name = str(name or "").strip()

        if not name:
            return ""

        # 替换 Windows 不允许的字符: < > : " / \ | ? *
        name = re.sub(r'[<>:"/\\|?*]', "_", name)

        # 替换控制字符
        name = re.sub(r"[\x00-\x1f]", "_", name)

        # Windows 不建议以空格或点结尾
        name = name.rstrip(" .")

        # 防止特殊目录
        if name in {"", ".", ".."}:
            return ""

        # Windows 保留名
        reserved_names = {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        }

        if name.upper() in reserved_names:
            name = f"_{name}"

        # 避免过长路径名
        if len(name) > 180:
            name = name[:180].rstrip(" .")

        return name

    def get_bms_output_subdir_from_record(
        self,
        record: dict[str, Any] | None,
    ) -> str:
        """
        从 beatmap_info.json 的单条记录中提取 BMS 输出子目录名。

        示例:
            osu_maps\\9650304 [aci]\\1526196 Camellia - Kisaragi.osz

        返回:
            9650304 [aci]
        """

        if not isinstance(record, dict):
            return ""

        download = record.get("download")

        if not isinstance(download, dict):
            return ""

        file_path = download.get("file_path") or ""
        file_path = str(file_path or "").strip()

        if not file_path:
            return ""

        try:
            # 使用 PureWindowsPath 是为了兼容 JSON 里的反斜杠路径
            parent_name = PureWindowsPath(file_path).parent.name
        except Exception:
            parent_name = ""

        parent_name = self.sanitize_folder_name(parent_name)

        return parent_name

    def get_info_mode_bms_output_subdir_from_source_meta(
        self,
        source_meta: dict[str, Any] | None,
    ) -> str:
        """
        只用于 info 模式。
        从 source_meta 中提取 BMS 输出子目录名。

        优先级:
            1. source_meta["bms_output_subdir"]
            2. source_meta["source_osz_path"] 的父目录名
        """

        if not isinstance(source_meta, dict):
            return ""

        # 1. 优先使用显式字段
        subdir = source_meta.get("bms_output_subdir") or ""
        subdir = self.sanitize_folder_name(str(subdir or ""))

        if subdir:
            return subdir

        # 2. 回退：从 source_osz_path 里提取父目录
        source_osz_path = (
            source_meta.get("source_osz_path")
            or source_meta.get("osz_path")
            or ""
        )

        source_osz_path = str(source_osz_path or "").strip()

        if not source_osz_path:
            return ""

        try:
            parent_name = Path(source_osz_path).parent.name
        except Exception:
            parent_name = ""

        parent_name = self.sanitize_folder_name(parent_name)

        return parent_name


    def run_bms_convert_and_analysis(
        self,
        osu_file: Path,
        analyze_bms: bool,
        *,
        source_meta: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        BMS 转换入口。

        重要规则：
            只有 info 模式才把 BMS 输出到子目录。
            其他模式仍然输出到 self.bms_output_dir。

        info 模式示例:
            self.bms_output_dir:
                E:/_BMS_/SONGS/OSU_PACK_QUEUE/test

            beatmap_info file_path:
                osu_maps\\9650304 [aci]\\1526196 Camellia - Kisaragi.osz

            最终 bms_output_dir:
                E:/_BMS_/SONGS/OSU_PACK_QUEUE/test/9650304 [aci]
        """

        base_bms_output_dir = Path(self.bms_output_dir)
        final_bms_output_dir = base_bms_output_dir

        current_mode = getattr(self, "current_mode", None)

        # ------------------------------------------------------------
        # 只有 info 模式启用子目录输出
        # ------------------------------------------------------------
        if current_mode == "info":
            subdir = self.get_info_mode_bms_output_subdir_from_source_meta(
                source_meta
            )

            if subdir:
                final_bms_output_dir = base_bms_output_dir / subdir
                self.log(f"[BMS] info 模式输出子目录: {subdir}")
            else:
                self.log("[BMS] info 模式未取得输出子目录，使用基础 BMS 输出目录")

        else:
            # 其他模式明确不追加子目录
            self.log("[BMS] 非 info 模式，使用基础 BMS 输出目录")

        try:
            final_bms_output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.log(f"[BMS WARN] 创建 BMS 输出目录失败: {final_bms_output_dir}")
            self.log(f"[BMS WARN] {exc}")
            # 目录创建失败时，回退到基础目录
            final_bms_output_dir = base_bms_output_dir
            final_bms_output_dir.mkdir(parents=True, exist_ok=True)

        self.log(f"[BMS] 输出目录: {final_bms_output_dir}")

        return bms_utils.run_bms_convert_and_analysis(
            osu_file=osu_file,
            analyze_bms=analyze_bms,
            output_bms=self.output_bms,
            output_dir=self.output_dir,
            bms_output_dir=final_bms_output_dir,
            log_func=self.log,
        )



    def build_bms_conversion_options(
        self,
        osu_file: Path,
        analyze_bms: bool,
    ) -> ConversionOptions:
        return bms_utils.build_bms_conversion_options(
            osu_file=osu_file,
            analyze_bms=analyze_bms,
        )

    def print_bms_conversion_result(self, result, analyze_bms: bool):
        return bms_utils.log_bms_conversion_result(
            result=result,
            analyze_bms=analyze_bms,
            log_func=self.log,
        )

    def _run_safe(self):
        try:
            self.validate()
            self.run()
        except Exception as exc:
            self.log(f"[GUI ERROR] {exc}")
        finally:
            self.running = False
            self.cleanup_temp_dir()

            if self.finish_callback:
                self.finish_callback()

    def run(self):
        """
        入口：
            - input_mode == "info"   -> run_info_file()
            - input_mode == "folder" -> run_batch()
            - input_mode == "file"   -> 原有单文件 / 单 .osz 流程
        """

        if self.input_mode == "info":
            self.run_info_file()
            return

        if self.batch_mode:
            self.run_batch()
            return

        osu_files = self.collect_osu_files()

        self.log("")
        self.log("=" * 70)
        self.log("[GUI] 混合难度分析开始")
        self.log(f"[GUI] 找到 {len(osu_files)} 个 .osu 文件")
        self.log(f"[GUI] JSON 保存目录: {self.output_dir}")
        self.log("=" * 70)

        completed = 0
        bms_completed = 0
        merged_items: list[Any] = []

        source_type = "osu"
        source_osz_path = None
        source_osz_sha256 = None

        if self.input_file and self.input_file.suffix.lower() == ".osz":
            source_type = "osz"
            source_osz_path = self.input_file

            try:
                source_osz_sha256 = self.file_sha256(self.input_file)
            except Exception as exc:
                self.log(f"[GUI WARN] 计算 osz_sha256 失败: {exc}")

        for idx, osu_file in enumerate(osu_files, 1):
            if not self.running:
                self.log("[GUI] 任务已中断")
                break

            self.log("")
            self.log(f"[{idx}/{len(osu_files)}] {osu_file.name}")
            self.log("-" * 70)

            source_meta = self.build_source_meta(
                source_type=source_type,
                osu_file=osu_file,
                source_osz_path=source_osz_path,
                source_osz_sha256=source_osz_sha256,
            )

            mixed_ok, bms_ok, saved_item = self.process_one_osu_file(
                osu_file,
                source_meta=source_meta,
                save_file=self.save_individual_json,
                print_summary=not self.quiet_analysis_logs,
                log_result=self.save_individual_json and not self.quiet_analysis_logs,
            )

            if mixed_ok:
                completed += 1

            if bms_ok:
                bms_completed += 1

            if saved_item is not None:
                merged_items.append(saved_item)

        self.log("")
        self.log("=" * 70)

        if self.running:
            self.log(f"[GUI] 全部完成，共 mixed 分析 {completed}/{len(osu_files)} 个谱面")
            self.log(f"[BMS] 共 BMS 转换 {bms_completed}/{len(osu_files)} 个谱面")
        else:
            self.log(f"[GUI] 已停止，已完成 mixed 分析 {completed}/{len(osu_files)} 个谱面")

        if self.merge_json_results and merged_items:
            self.save_merged_json_results(merged_items)

    # ------------------------------------------------------------------
    # batch flow
    # ------------------------------------------------------------------

    def run_batch(self):
        """
        批量处理文件夹下所有 .osu / .osz。
        文件夹模式保持原含义，不自动消费 beatmap_info.json。
        beatmap_info.json 请使用 GUI 的“下载器 JSON”入口。
        """

        input_files = self.collect_batch_input_files()

        self.log("")
        self.log("=" * 70)
        self.log("[GUI] 批量混合难度分析开始")
        self.log(f"[GUI] 输入文件夹: {self.input_dir}")
        self.log(f"[GUI] 找到 {len(input_files)} 个 .osu / .osz 文件")
        self.log(f"[GUI] JSON 保存目录: {self.output_dir}")
        self.log("=" * 70)

        if not input_files:
            self.log("[GUI] 未找到可处理文件")
            return

        total_mixed_count = 0
        total_bms_count = 0
        total_osu_count = 0

        merged_items: list[Any] = []

        for file_idx, input_file in enumerate(input_files, 1):
            if not self.running:
                self.log("[GUI] 批量任务已中断")
                break

            self.log("")
            self.log("#" * 70)
            self.log(f"[GUI] 批量文件 [{file_idx}/{len(input_files)}]: {input_file}")
            self.log("#" * 70)

            self.input_file = input_file

            source_type = "osu"
            source_osz_path = None
            source_osz_sha256 = None

            if input_file.suffix.lower() == ".osz":
                source_type = "osz"
                source_osz_path = input_file

                try:
                    source_osz_sha256 = self.file_sha256(input_file)
                except Exception as exc:
                    self.log(f"[GUI WARN] 计算 osz_sha256 失败: {exc}")

            try:
                osu_files = self.collect_osu_files()
            except Exception as exc:
                self.log(f"[GUI ERROR] 收集 osu 文件失败: {exc}")
                continue

            if not osu_files:
                self.log(f"[GUI] 跳过，没有找到 .osu: {input_file}")
                continue

            self.log(f"[GUI] 当前文件展开后包含 {len(osu_files)} 个 .osu")
            total_osu_count += len(osu_files)

            for osu_idx, osu_file in enumerate(osu_files, 1):
                if not self.running:
                    self.log("[GUI] 批量任务已中断")
                    break

                self.log("")
                self.log(
                    f"[{file_idx}/{len(input_files)}] "
                    f"[{osu_idx}/{len(osu_files)}] {osu_file.name}"
                )
                self.log("-" * 70)

                source_meta = self.build_source_meta(
                    source_type=source_type,
                    osu_file=osu_file,
                    source_osz_path=source_osz_path,
                    source_osz_sha256=source_osz_sha256,
                )

                mixed_ok, bms_ok, saved_item = self.process_one_osu_file(
                    osu_file,
                    source_meta=source_meta,
                    save_file=self.save_individual_json,
                    print_summary=not self.quiet_analysis_logs,
                    log_result=self.save_individual_json and not self.quiet_analysis_logs,
                )

                if mixed_ok:
                    total_mixed_count += 1

                if bms_ok:
                    total_bms_count += 1

                if saved_item is not None:
                    merged_items.append(saved_item)

            self.cleanup_temp_dir()

        self.log("")
        self.log("=" * 70)

        if self.running:
            self.log("[GUI] 批量分析完成")
            self.log(f"[GUI] 共展开 .osu 谱面: {total_osu_count}")
            self.log(f"[GUI] 共 mixed 分析成功: {total_mixed_count}/{total_osu_count}")
            self.log(f"[BMS] 共 BMS 转换成功: {total_bms_count}/{total_osu_count}")
        else:
            self.log("[GUI] 批量分析已停止")
            self.log(f"[GUI] 已 mixed 分析成功: {total_mixed_count}/{total_osu_count}")
            self.log(f"[BMS] 已 BMS 转换成功: {total_bms_count}/{total_osu_count}")

        if self.merge_json_results and merged_items:
            self.save_merged_json_results(merged_items)
        elif self.merge_json_results:
            self.log("[GUI] 没有可合并的 JSON 结果")

    def collect_batch_input_files(self) -> list[Path]:
        """
        递归收集 input_dir 下所有 .osu / .osz 文件。
        注意：不在文件夹模式下自动处理 beatmap_info.json，避免重复分析。
        """

        if self.input_dir is None:
            return []

        result: list[Path] = []

        for path in self.input_dir.rglob("*"):
            if not self.running:
                break

            if not path.is_file():
                continue

            if path.suffix.lower() in [".osu", ".osz"]:
                result.append(path)

        return sorted(result, key=lambda p: str(p).lower())

    # ------------------------------------------------------------------
    # beatmap_info.json flow
    # ------------------------------------------------------------------

    def load_beatmap_info_file(self, info_path: Path) -> dict[str, Any]:
        """
        读取下载器 beatmap_info.json。
        """

        try:
            data = json.loads(Path(info_path).read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"读取 beatmap_info.json 失败: {exc}")

        if not isinstance(data, dict):
            raise ValueError("beatmap_info.json 顶层必须是 object")

        beatmapsets = data.get("beatmapsets")

        if not isinstance(beatmapsets, dict):
            raise ValueError("beatmap_info.json 缺少 beatmapsets object")

        return data


    def iter_beatmap_info_records(self, info_path: Path):
        """
        遍历 beatmap_info.json 中的 beatmapsets。

        """

        data = self.load_beatmap_info_file(info_path)
        beatmapsets = data.get("beatmapsets")

        if not isinstance(beatmapsets, dict):
            return

        for key, record in beatmapsets.items():
            if not isinstance(record, dict):
                continue

            beatmapset_id = record.get("beatmapset_id") or key
            yield str(beatmapset_id), record


    def resolve_osz_path_from_record(
        self,
        info_path: Path,
        record: dict[str, Any],
    ) -> Path:
        """
        根据 beatmap_info.json 中的 download.file_path 定位 .osz 文件。

        规则：
            1. 只使用 download.file_path。
            2. 如果 file_path 是绝对路径，直接使用。
            3. 如果 file_path 是相对路径，则相对于 beatmap_info.json 所在目录。
        """

        download = record.get("download")
        if not isinstance(download, dict):
            raise ValueError("beatmap_info 记录缺少 download 字段")

        file_path_text = download.get("file_path")
        if not file_path_text:
            raise ValueError("beatmap_info 记录缺少 download.file_path 字段")

        normalized_text = str(file_path_text).replace("\\", "/")
        raw_path = Path(normalized_text)

        if raw_path.is_absolute():
            osz_path = raw_path
        else:
            osz_path = info_path.parent / raw_path

        try:
            osz_path = osz_path.resolve()
        except Exception:
            pass

        if not osz_path.exists():
            raise FileNotFoundError(
                "download.file_path 指向的 .osz 文件不存在。\n"
                f"beatmapset_id: {record.get('beatmapset_id')}\n"
                f"file_path: {file_path_text}\n"
                f"解析路径: {osz_path}"
            )

        if not osz_path.is_file():
            raise FileNotFoundError(
                "download.file_path 指向的路径不是文件。\n"
                f"beatmapset_id: {record.get('beatmapset_id')}\n"
                f"file_path: {file_path_text}\n"
                f"解析路径: {osz_path}"
            )

        if osz_path.suffix.lower() != ".osz":
            raise ValueError(
                "download.file_path 指向的文件不是 .osz。\n"
                f"beatmapset_id: {record.get('beatmapset_id')}\n"
                f"file_path: {file_path_text}\n"
                f"解析路径: {osz_path}"
            )

        return osz_path

    def load_final_result_md5_index(
        self,
        final_result_path: Path | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        加载已有 final_result.json，并建立 osu_md5 -> item 的索引。

        支持两种结构：

        1. 顶层是 list:
        [
            {"title": "...", "osu_md5": "..."},
            ...
        ]

        2. 顶层是 dict:
        {
            "items": [
            {"title": "...", "osu_md5": "..."},
            ...
            ]
        }
        """

        if final_result_path is None:
            final_result_path = Path(self.output_dir) / "final_result.json"

        final_result_path = Path(final_result_path)

        if not final_result_path.exists() or not final_result_path.is_file():
            self.log(f"[GUI] 未找到已有 final_result.json: {final_result_path}")
            return {}

        try:
            payload = json.loads(
                final_result_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            self.log(f"[GUI WARN] 读取 final_result.json 失败: {exc}")
            return {}

        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            raw_items = payload.get("items")
            if isinstance(raw_items, list):
                items = raw_items
            else:
                self.log("[GUI WARN] final_result.json 中没有有效 items")
                return {}
        else:
            self.log("[GUI WARN] final_result.json 格式不是 list/dict")
            return {}

        md5_index: dict[str, dict[str, Any]] = {}

        for item in items:
            if not isinstance(item, dict):
                continue

            osu_md5 = (
                item.get("osu_md5")
                or item.get("source_osu_md5")
                or item.get("md5")
                or ""
            )

            osu_md5 = str(osu_md5 or "").strip().lower()

            if not osu_md5:
                continue

            md5_index[osu_md5] = item

        self.log(f"[GUI] 已加载 final_result md5 索引: {len(md5_index)} 条")

        return md5_index
    
    def find_existing_final_result_by_osu_md5(
        self,
        source_meta: dict[str, Any] | None,
        final_result_md5_index: dict[str, dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """
        根据当前 source_meta 中的 osu_md5/source_osu_md5，
        从 final_result_md5_index 中查找已有分析结果。
        """

        if not isinstance(source_meta, dict):
            return None

        if not isinstance(final_result_md5_index, dict):
            return None

        osu_md5 = (
            source_meta.get("osu_md5")
            or source_meta.get("source_osu_md5")
            or ""
        )

        osu_md5 = str(osu_md5 or "").strip().lower()

        if not osu_md5:
            return None

        item = final_result_md5_index.get(osu_md5)

        if not isinstance(item, dict):
            return None

        return item

    def run_info_file(self):
        """
        处理下载器 beatmap_info.json。

        info 模式下：
            BMS 输出目录会使用 source_meta["bms_output_subdir"]。
            例如：
                GUI 传入:
                    E:/_BMS_/SONGS/OSU_PACK_QUEUE/test

                beatmap_info:
                    osu_maps\\9650304 [aci]\\1526196 Camellia - Kisaragi.osz

                最终 BMS 输出目录:
                    E:/_BMS_/SONGS/OSU_PACK_QUEUE/test/9650304 [aci]

        注意：
            是否真的追加子目录，由 run_bms_convert_and_analysis()
            根据 self.current_mode == "info" 判断。
        """

        if self.input_info_file is None:
            raise FileNotFoundError("未指定 beatmap_info.json")

        previous_mode = getattr(self, "current_mode", None)
        self.current_mode = "info"

        try:
            records = list(self.iter_beatmap_info_records(self.input_info_file))

            self.log("")
            self.log("=" * 70)
            self.log("[GUI] 下载器 beatmap_info.json 分析开始")
            self.log(f"[GUI] beatmap_info: {self.input_info_file}")
            self.log(f"[GUI] 记录数量: {len(records)}")
            self.log(f"[GUI] JSON 保存目录: {self.output_dir}")
            self.log(f"[BMS] BMS 基础输出目录: {self.bms_output_dir}")
            self.log("=" * 70)

            if not records:
                self.log("[GUI] beatmap_info.json 中没有有效记录")
                return

            # ------------------------------------------------------------
            # 启动时加载已有 final_result.json，并建立 osu_md5 索引
            # ------------------------------------------------------------
            final_result_md5_index = self.load_final_result_md5_index(
                Path(self.output_dir) / "final_result.json"
            )

            total_osu_count = 0
            total_mixed_count = 0
            total_bms_count = 0
            total_osz_count = 0
            total_skipped_count = 0

            merged_items: list[Any] = []

            for record_idx, (beatmapset_id, record) in enumerate(records, 1):
                if not self.running:
                    self.log("[GUI] beatmap_info 分析已中断")
                    break

                self.log("")
                self.log("#" * 70)
                self.log(f"[GUI] beatmapset [{record_idx}/{len(records)}]: {beatmapset_id}")
                self.log("#" * 70)

                try:
                    osz_path = self.resolve_osz_path_from_record(
                        self.input_info_file,
                        record,
                    )
                except Exception as exc:
                    self.log(f"[GUI ERROR] 定位 .osz 失败: {exc}")
                    continue

                total_osz_count += 1

                try:
                    osz_sha256 = self.file_sha256(osz_path)
                except Exception as exc:
                    self.log(f"[GUI WARN] 计算 osz_sha256 失败: {exc}")
                    osz_sha256 = None

                self.log(f"[GUI] .osz: {osz_path}")
                if osz_sha256:
                    self.log(f"[GUI] osz_sha256: {osz_sha256}")

                # 当前 beatmap_info 记录对应的 BMS 输出子目录
                bms_output_subdir = self.get_bms_output_subdir_from_record(record)
                if bms_output_subdir:
                    self.log(f"[BMS] 当前 .osz 输出子目录: {bms_output_subdir}")
                else:
                    self.log("[BMS] 当前 .osz 未取得输出子目录，将使用基础 BMS 输出目录")

                try:
                    osu_files = self.extract_osz(osz_path)
                except Exception as exc:
                    self.log(f"[GUI ERROR] 解压 .osz 失败: {exc}")
                    continue

                if not osu_files:
                    self.log(f"[GUI] 跳过，没有找到 .osu: {osz_path}")
                    self.cleanup_temp_dir()
                    continue

                self.log(f"[GUI] 当前 .osz 包含 {len(osu_files)} 个 .osu")
                total_osu_count += len(osu_files)

                for osu_idx, osu_file in enumerate(osu_files, 1):
                    if not self.running:
                        self.log("[GUI] beatmap_info 分析已中断")
                        break

                    self.log("")
                    self.log(
                        f"[{record_idx}/{len(records)}] "
                        f"[{osu_idx}/{len(osu_files)}] {osu_file.name}"
                    )
                    self.log("-" * 70)

                    source_meta = self.build_source_meta(
                        source_type="beatmap_info",
                        osu_file=osu_file,
                        source_osz_path=osz_path,
                        source_osz_sha256=osz_sha256,
                        beatmap_info_path=self.input_info_file,
                        beatmap_info_record=record,
                    )

                    # ------------------------------------------------------------
                    # info 模式专用：
                    # 传给 run_bms_convert_and_analysis()，
                    # 让 BMS 输出到 self.bms_output_dir / bms_output_subdir
                    # ------------------------------------------------------------
                    source_meta["bms_output_subdir"] = bms_output_subdir

                    # ------------------------------------------------------------
                    # 解压得到 .osu 后，立刻用 osu_md5 查 final_result.json。
                    # 如果命中，则注入新的 metadata，跳过实际分析。
                    # ------------------------------------------------------------
                    existing_final_item = self.find_existing_final_result_by_osu_md5(
                        source_meta,
                        final_result_md5_index,
                    )

                    if existing_final_item is not None:
                        osu_md5 = (
                            source_meta.get("osu_md5")
                            or source_meta.get("source_osu_md5")
                            or ""
                        )

                        self.log(
                            f"[GUI] 命中 final_result.json 既有结果，跳过分析: {osu_file.name}"
                        )
                        self.log(f"[GUI] osu_md5: {osu_md5}")

                        skipped_item = deepcopy(existing_final_item)

                        # 重新注入当前 beatmap_info 的 metadata
                        skipped_item = self.inject_source_fields_to_output(
                            skipped_item,
                            source_meta,
                        )

                        merged_items.append(skipped_item)

                        total_skipped_count += 1

                        # 这个谱面已有结果，视作 mixed 成功
                        total_mixed_count += 1

                        # 如果旧 item 里有 BMS 结果，可以视作 bms 成功
                        if skipped_item.get("bms") or skipped_item.get("bms_difficulty"):
                            total_bms_count += 1

                        continue

                    # ------------------------------------------------------------
                    # 没有命中旧 final_result，则正常分析
                    #
                    # 注意：
                    # 不要在这里直接调用 run_bms_convert_and_analysis()。
                    # 应该走 process_one_osu_file()，
                    # 因为它内部会先跑 mixed，再决定是否跑 BMS。
                    # ------------------------------------------------------------
                    mixed_ok, bms_ok, saved_item = self.process_one_osu_file(
                        osu_file=osu_file,
                        source_meta=source_meta,
                        save_file=self.save_individual_json,
                        print_summary=not self.quiet_analysis_logs,
                        log_result=self.save_individual_json and not self.quiet_analysis_logs,
                    )

                    if mixed_ok:
                        total_mixed_count += 1

                    if bms_ok:
                        total_bms_count += 1

                    if saved_item is not None:
                        merged_items.append(saved_item)

                self.cleanup_temp_dir()

            self.log("")
            self.log("=" * 70)

            if self.running:
                self.log("[GUI] beatmap_info.json 分析完成")
                self.log(f"[GUI] 共处理 .osz: {total_osz_count}")
                self.log(f"[GUI] 共展开 .osu 谱面: {total_osu_count}")
                self.log(f"[GUI] 命中 final_result 跳过: {total_skipped_count}")
                self.log(f"[GUI] 共 mixed 分析成功: {total_mixed_count}/{total_osu_count}")
                self.log(f"[BMS] 共 BMS 转换成功: {total_bms_count}/{total_osu_count}")
            else:
                self.log("[GUI] beatmap_info.json 分析已停止")
                self.log(f"[GUI] 命中 final_result 跳过: {total_skipped_count}")
                self.log(f"[GUI] 已 mixed 分析成功: {total_mixed_count}/{total_osu_count}")
                self.log(f"[BMS] 已 BMS 转换成功: {total_bms_count}/{total_osu_count}")

            if self.merge_json_results and merged_items:
                self.save_merged_json_results(merged_items)
            elif self.merge_json_results:
                self.log("[GUI] 没有可合并的 JSON 结果")

        finally:
            self.current_mode = previous_mode


    # ------------------------------------------------------------------
    # source meta
    # ------------------------------------------------------------------

    def build_source_meta(
        self,
        *,
        source_type: str,
        osu_file: Path,
        source_osz_path: Path | None = None,
        source_osz_sha256: str | None = None,
        beatmap_info_path: Path | None = None,
        beatmap_info_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        构造来源元数据。

        所有入口都会写入：
            osu_md5 / source_osu_md5
            analyzed_at
            downloaded_at
            last_updated

        非 beatmap_info 入口没有下载时间/更新时间时：
            downloaded_at = ""
            last_updated = ""
        """

        source_osu_md5 = ""

        try:
            source_osu_md5 = self.file_md5(osu_file)
        except Exception as exc:
            self.log(f"[GUI WARN] 计算 source_osu_md5 失败: {exc}")

        analyzed_at = self.now_iso()

        meta: dict[str, Any] = {
            "source_type": source_type,

            # 原始 .osu 文件信息
            "source_osu_path": str(osu_file),
            "source_osu_md5": source_osu_md5,

            # 兼容你要求的字段名
            "osu_md5": source_osu_md5,

            # 所有模式都有分析时间
            "analyzed_at": analyzed_at,

            # 默认没有下载/更新时间
            "downloaded_at": "",
            "last_updated": "",

            "analyzer_version": self.analyzer_version,
        }

        if source_osz_path is not None:
            meta["source_osz_path"] = str(source_osz_path)

        if source_osz_sha256:
            meta["source_osz_sha256"] = source_osz_sha256

        if beatmap_info_path is not None:
            meta["beatmap_info_path"] = str(beatmap_info_path)

        if isinstance(beatmap_info_record, dict):
            time_info = beatmap_info_record.get("time")
            if not isinstance(time_info, dict):
                time_info = {}

            download_info = beatmap_info_record.get("download")
            if not isinstance(download_info, dict):
                download_info = {}

            submitted_date = time_info.get("submitted_date") or ""
            last_updated = time_info.get("last_updated") or ""
            downloaded_at = time_info.get("downloaded_at") or ""

            meta["beatmapset_id"] = beatmap_info_record.get("beatmapset_id")
            meta["official_url"] = beatmap_info_record.get("official_url") or ""

            meta["song"] = beatmap_info_record.get("song") or {}
            meta["mapper"] = beatmap_info_record.get("mapper") or {}

            meta["submitted_date"] = submitted_date
            meta["last_updated"] = last_updated
            meta["downloaded_at"] = downloaded_at

            meta["download_filename"] = download_info.get("filename") or ""
            meta["download_file_path"] = download_info.get("file_path") or ""
            meta["download_source"] = download_info.get("source") or ""
            meta["download_url"] = download_info.get("url") or ""
            meta["download_with_video"] = download_info.get("with_video")

        return meta
    
    def get_analysis_index_path(self) -> Path:
        return self.output_dir / self.analysis_index_filename
    
    def load_analysis_index(self) -> dict[str, Any]:
        path = self.get_analysis_index_path()

        if not path.exists():
            return {
                "version": 1,
                "items": {},
            }

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log(f"[GUI WARN] 读取 analysis_index.json 失败，将重建: {exc}")
            return {
                "version": 1,
                "items": {},
            }

        if not isinstance(data, dict):
            return {
                "version": 1,
                "items": {},
            }

        if not isinstance(data.get("items"), dict):
            data["items"] = {}

        if "version" not in data:
            data["version"] = 1

        return data
    
    def save_analysis_index(self, index: dict[str, Any]):
        path = self.get_analysis_index_path()

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            path.write_text(
                json.dumps(index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        except Exception as exc:
            self.log(f"[GUI WARN] 保存 analysis_index.json 失败: {exc}")
    
    def make_analysis_key(self, source_osu_md5: str) -> str:
        source_osu_md5 = str(source_osu_md5 or "").strip()

        return (
            f"osu_md5:{source_osu_md5}"
            f"|analyzer:{self.analyzer_version}"
        )

    def find_existing_analysis(
        self,
        source_osu_md5: str,
    ) -> dict[str, Any] | None:
        if not self.enable_analysis_skip:
            return None

        source_osu_md5 = str(source_osu_md5 or "").strip()
        if not source_osu_md5:
            return None

        index = self.load_analysis_index()
        key = self.make_analysis_key(source_osu_md5)

        item = index.get("items", {}).get(key)

        if not isinstance(item, dict):
            return None

        if item.get("status") != "success":
            return None

        result_path = item.get("result_path")
        if not result_path:
            return None

        path = Path(result_path)

        if not path.exists() or not path.is_file():
            return None

        return item

    def inject_source_fields_to_output(
        self,
        data_to_save: Any,
        source_meta: dict[str, Any] | None,
    ) -> Any:
        """
        将来源字段直接写入每个谱面最终结果对象。

        最终只新增这些字段：

            osu_md5
            downloaded_at
            last_updated
            analyzed_at
            analyzer_version

        不写入：
            source
            source_osu_md5
        """

        if not isinstance(data_to_save, dict):
            return data_to_save

        if not isinstance(source_meta, dict):
            source_meta = {}

        osu_md5 = (
            source_meta.get("source_osu_md5")
            or source_meta.get("osu_md5")
            or ""
        )

        downloaded_at = source_meta.get("downloaded_at") or ""
        last_updated = source_meta.get("last_updated") or ""
        analyzed_at = source_meta.get("analyzed_at") or self.now_iso()
        analyzer_version = source_meta.get("analyzer_version") or self.analyzer_version

        data_to_save["osu_md5"] = osu_md5
        data_to_save["downloaded_at"] = downloaded_at
        data_to_save["last_updated"] = last_updated
        data_to_save["analyzed_at"] = analyzed_at
        data_to_save["analyzer_version"] = analyzer_version

        return data_to_save

    
    def load_existing_result_for_merge(
        self,
        index_item: dict[str, Any],
    ) -> Any | None:
        result_path = index_item.get("result_path")

        if not result_path:
            return None

        path = Path(result_path)

        if not path.exists() or not path.is_file():
            return None

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log(f"[GUI WARN] 读取已存在 JSON 失败，无法加入合并: {exc}")
            return None

    def update_analysis_index_success(
        self,
        *,
        source_meta: dict[str, Any],
        result_path: Path | None,
        osu_file: Path,
    ):
        if not self.enable_analysis_skip:
            return

        source_osu_md5 = (
            source_meta.get("source_osu_md5")
            or source_meta.get("osu_md5")
            or ""
        )

        source_osu_md5 = str(source_osu_md5).strip()

        if not source_osu_md5:
            return

        if result_path is None:
            return

        result_path = Path(result_path)

        if not result_path.exists() or not result_path.is_file():
            return

        index = self.load_analysis_index()
        key = self.make_analysis_key(source_osu_md5)

        index["items"][key] = {
            "status": "success",

            "analysis_key": key,

            "source_osu_md5": source_osu_md5,
            "osu_md5": source_osu_md5,

            "source_type": source_meta.get("source_type") or "",
            "source_osu_path": source_meta.get("source_osu_path") or "",
            "source_osz_path": source_meta.get("source_osz_path") or "",
            "source_osz_sha256": source_meta.get("source_osz_sha256") or "",

            "beatmapset_id": source_meta.get("beatmapset_id"),
            "official_url": source_meta.get("official_url") or "",

            "osu_name": Path(osu_file).name,

            "downloaded_at": source_meta.get("downloaded_at") or "",
            "last_updated": source_meta.get("last_updated") or "",
            "analyzed_at": source_meta.get("analyzed_at") or self.now_iso(),

            "result_path": str(result_path),

            "analyzer_version": self.analyzer_version,
        }

        self.save_analysis_index(index)


    # ------------------------------------------------------------------
    # common process one osu
    # ------------------------------------------------------------------

    def process_one_osu_file(
        self,
        osu_file: Path,
        *,
        source_meta: dict[str, Any] | None = None,
        save_file: bool | None = None,
        print_summary: bool | None = None,
        log_result: bool | None = None,
    ) -> tuple[bool, bool, Any | None]:
        """
        保留原核心调用链：

            run_node_process()
            should_run_bms_after_mixed()
            run_bms_convert_and_analysis()
            save_json_and_print_summary()

        新增：
            使用 source_osu_md5 + analyzer_version 跳过已分析谱面。

        注意：
            BMS 输出子目录逻辑不在这里判断。
            这里只负责把 source_meta 传给 run_bms_convert_and_analysis()。
            是否启用 info 子目录，由 run_bms_convert_and_analysis() 根据 current_mode 判断。

        Returns:
            mixed_ok, bms_ok, saved_item
        """

        if save_file is None:
            save_file = self.save_individual_json

        if print_summary is None:
            print_summary = not self.quiet_analysis_logs

        if log_result is None:
            log_result = self.save_individual_json and not self.quiet_analysis_logs

        # ------------------------------------------------------------
        # 1. 跳过判断
        # ------------------------------------------------------------
        source_osu_md5 = ""

        if isinstance(source_meta, dict):
            source_osu_md5 = (
                source_meta.get("source_osu_md5")
                or source_meta.get("osu_md5")
                or ""
            )

        source_osu_md5 = str(source_osu_md5).strip()

        if source_osu_md5:
            existing = self.find_existing_analysis(source_osu_md5)

            if existing:
                self.log(f"[SKIP] 已分析，跳过: {Path(osu_file).name}")
                self.log(f"[SKIP] osu_md5: {source_osu_md5}")
                self.log(f"[SKIP] analyzer_version: {self.analyzer_version}")

                result_path = existing.get("result_path")
                if result_path:
                    self.log(f"[SKIP] 使用旧结果: {result_path}")

                existing_data = self.load_existing_result_for_merge(existing)

                if existing_data is not None:
                    return True, False, existing_data

                # 如果 index 有记录，但旧 JSON 读取失败，则继续重新分析
                self.log("[SKIP] 旧结果不可用，将重新分析")

        # ------------------------------------------------------------
        # 2. 原 mixed 分析
        # ------------------------------------------------------------
        ok, data = self.run_node_process(osu_file)

        if not isinstance(data, dict):
            data = {}

        # ------------------------------------------------------------
        # 3. 原 BMS 判断
        # ------------------------------------------------------------
        should_convert_bms, should_analyze_bms, reason = self.should_run_bms_after_mixed(data)

        bms_payload = None
        bms_ok = False

        if should_convert_bms:
            self.log("")
            self.log(f"[BMS] 触发 BMS 转换: {reason}")

            bms_ok, bms_payload = self.run_bms_convert_and_analysis(
                osu_file=osu_file,
                analyze_bms=should_analyze_bms,
                source_meta=source_meta,
            )
        else:
            self.log("")
            self.log(f"[BMS] 跳过 BMS 转换/分析: {reason}")
            bms_payload = bms_utils.build_empty_bms_payload(
                output_bms=self.output_bms,
                reason=reason,
            )

        # ------------------------------------------------------------
        # 4. 保存 JSON
        # ------------------------------------------------------------
        saved_result = self.save_json_and_print_summary(
            data=data,
            osu_file=osu_file,
            bms_payload=bms_payload,
            source_meta=source_meta,
            save_file=save_file,
            print_summary=print_summary,
            log_result=log_result,
            return_with_path=True,
        )

        saved_item = None
        result_path = None

        if isinstance(saved_result, dict):
            saved_item = saved_result.get("data")

            result_path_text = saved_result.get("path")
            if result_path_text:
                result_path = Path(result_path_text)
        else:
            saved_item = saved_result

        # ------------------------------------------------------------
        # 5. 成功后更新 index
        # ------------------------------------------------------------
        if ok and isinstance(source_meta, dict):
            self.update_analysis_index_success(
                source_meta=source_meta,
                result_path=result_path,
                osu_file=osu_file,
            )

        return bool(ok), bool(bms_ok), saved_item



    # ------------------------------------------------------------------
    # osu / osz
    # ------------------------------------------------------------------

    def collect_osu_files(self):
        if self.input_file is None:
            return []

        if self.input_file.suffix.lower() == ".osz":
            osu_files = self.extract_osz(self.input_file)

            if not osu_files:
                raise ValueError(".osz 中没有找到 .osu 文件")

            return osu_files

        return [self.input_file]

    def extract_osz(self, osz_path: Path):
        self.cleanup_temp_dir()

        self.temp_dir = tempfile.mkdtemp(prefix="osz_")

        with zipfile.ZipFile(osz_path, "r") as z:
            z.extractall(self.temp_dir)

        osu_files = list(Path(self.temp_dir).rglob("*.osu"))
        return sorted(osu_files)

    def cleanup_temp_dir(self):
        if self.temp_dir:
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass
            finally:
                self.temp_dir = None

    # ------------------------------------------------------------------
    # node
    # ------------------------------------------------------------------

    def run_node_process(self, osu_file: Path):
        cmd = [
            "node",
            str(self.runner_file),
            str(self.project_root),
            str(osu_file),
            str(self.speed_rate),
            self.cvt_flag,
            "true" if self.with_graph else "false",
        ]

        stdout_chunks = []

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            stdout_thread = threading.Thread(
                target=self.read_stream,
                args=(self.process.stdout, "[stdout]", stdout_chunks),
                daemon=True,
            )

            stderr_thread = threading.Thread(
                target=self.read_stream,
                args=(self.process.stderr, "[log]", None),
                daemon=True,
            )

            stdout_thread.start()
            stderr_thread.start()

            exit_code = self.process.wait()

            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)

            full_stdout = "".join(stdout_chunks)

            data = json_utils.extract_json_from_stdout(full_stdout)

            if data is None:
                self.log("[GUI ERROR] 无法从 Node stdout 提取 mixed JSON")
                return False, None

            return exit_code == 0, data

        except FileNotFoundError:
            self.log("[GUI ERROR] 找不到 node 命令，请确认 Node.js 已安装并加入 PATH")
            return False, None

        except Exception as exc:
            self.log(f"[GUI ERROR] {exc}")
            return False, None

        finally:
            self.process = None

    def read_stream(self, stream, prefix, collect):
        try:
            for line in stream:
                if collect is not None:
                    collect.append(line)
                else:
                    if not self.summary_only:
                        self.log(f"{prefix} {line.rstrip()}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # json
    # ------------------------------------------------------------------

    def extract_json_from_stdout(self, stdout_text):
        return json_utils.extract_json_from_stdout(stdout_text)

    def make_unique_json_path(self, osu_file: Path):
        return json_utils.make_unique_json_path(
            self.output_dir,
            osu_file,
            suffix=".json",
        )
    
    def save_json_and_print_summary(
        self,
        data: dict[str, Any] | None,
        osu_file: Path,
        bms_payload: dict[str, Any] | None = None,
        source_meta: dict[str, Any] | None = None,
        skip_merge_output=False,
        save_file: bool = True,
        print_summary: bool = True,
        log_result: bool = True,
        return_with_path: bool = False,
    ):
        """
        保存最终处理后的 JSON，并打印摘要。

        正确流程：

            mixed data
                + bms payload
                -> prepare_final_result_source
                -> apply_field_mapping 根据 config 筛选最终字段
                -> 注入每个谱面必须保留的 metadata 字段
                -> save final json

        注意：
            metadata 字段必须在 apply_field_mapping 之后注入。
            因为 config 中通常没有 osu_md5 / downloaded_at / last_updated / analyzed_at / analyzer_version。
        """

        if not isinstance(data, dict) or not data:
            self.log("[GUI] mixed data 无效，未保存")
            return None

        raw_data = deepcopy(data)

        cleaned_data, summary_text = json_utils.remove_summary_text(raw_data)

        if isinstance(cleaned_data, dict):
            raw_data = cleaned_data

        # 合并 BMS payload
        if isinstance(bms_payload, dict):
            raw_data["bms"] = bms_payload

        # 不再把 source_meta 写进 raw_data["source"]
        # 因为你不希望最终 JSON 出现 source。
        # source_meta 只用于最后注入以下字段：
        #   osu_md5
        #   downloaded_at
        #   last_updated
        #   analyzed_at
        #   analyzer_version

        route_mode = json_utils.get_route_mode(raw_data)

        json_path = json_utils.make_unique_json_path(
            self.output_dir,
            osu_file,
            suffix=".mixed.json",
        )

        data_to_save = raw_data

        try:
            mapping_config_path = getattr(
                self,
                "final_result_mapping_config_path",
                None,
            )

            enable_final_result_mapping = bool(
                getattr(
                    self,
                    "enable_final_result_mapping",
                    False,
                )
            )

            if enable_final_result_mapping and mapping_config_path:
                mapping_config_path = Path(mapping_config_path)

                if not mapping_config_path.exists():
                    raise FileNotFoundError(
                        f"最终 JSON 字段映射配置不存在: {mapping_config_path}"
                    )

                config = json.loads(
                    mapping_config_path.read_text(encoding="utf-8")
                )

                processed_data = prepare_final_result_source(
                    raw_data,
                    remove_none=False,
                )

                derived = (
                    processed_data.get("derived")
                    if isinstance(processed_data, dict)
                    else None
                )

                derived_keys = (
                    derived.get("keys")
                    if isinstance(derived, dict)
                    else None
                )

                try:
                    derived_keys_int = int(derived_keys)
                except Exception:
                    derived_keys_int = None

                if derived_keys_int != 7:
                    skip_merge_output = True

                # 这里根据 config 生成最终字段。
                # 注意：config 里没有 metadata 字段没关系，
                # 因为 metadata 会在 mapping 完成之后统一注入。
                mapped_data = apply_field_mapping(
                    processed_data,
                    config,
                )

                if isinstance(mapped_data, dict):
                    data_to_save = mapped_data
                else:
                    self.log("[GUI WARN] apply_field_mapping 返回值不是 dict，将保存 raw_data")
                    data_to_save = raw_data

        except Exception as exc:
            self.log(
                f"[GUI] 最终 JSON 字段处理/映射失败，将保存原始合并 JSON: {exc}"
            )
            data_to_save = raw_data

        # ------------------------------------------------------------------
        # 关键修复点：
        #
        # metadata 必须在 apply_field_mapping 之后注入。
        # 这样即使 config 没有这些字段，最终 JSON 也一定会有。
        # ------------------------------------------------------------------
        data_to_save = self.inject_source_fields_to_output(
            data_to_save,
            source_meta,
        )

        if save_file:
            try:
                json_utils.save_json_file(
                    data_to_save,
                    json_path,
                )

                if log_result:
                    if raw_data.get("ok"):
                        self.log(f"[GUI] JSON 已保存: {json_path}")
                    else:
                        self.log(f"[GUI] 分析失败 JSON 已保存: {json_path}")
                        error_msg = raw_data.get("error")
                        if error_msg:
                            self.log(f"[GUI] 错误: {error_msg}")

            except Exception as exc:
                self.log(f"[GUI] 保存 JSON 失败: {exc}")
                return raw_data

        if summary_text and print_summary:
            self.log("")
            self.log(summary_text)

        if log_result:
            self.log(f"[GUI] route.mode: {route_mode!r}")

        merge_data = None if skip_merge_output else data_to_save

        if return_with_path:
            return {
                "data": merge_data,
                "path": str(json_path) if save_file else None,
                "skip_merge_output": bool(skip_merge_output),
            }

        return merge_data

    def save_merged_json_results(self, items: list[Any]) -> Path:
        path = self.output_dir / self.merged_json_name

        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.log(f"[GUI] 保存合并 JSON: {path}")

        return path


    # ------------------------------------------------------------------
    # callback
    # ------------------------------------------------------------------

    def log(self, text=""):
        text = str(text)

        if self.quiet_analysis_logs:
            normalized = text.lstrip().lower()

            # 过滤 Node stderr / BMS 过程日志
            if normalized.startswith("[log]"):
                return

            if normalized.startswith("[bms]"):
                return

            if normalized.startswith("bms "):
                return

        if self.log_callback:
            self.log_callback(text)
