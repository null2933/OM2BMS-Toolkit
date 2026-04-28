import json
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any
from copy import deepcopy

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

        # 新增：批量处理参数
        input_dir: Path | None = None,
        batch_mode: bool = False,
        merge_json_results: bool = True,
        save_individual_json: bool = True,
        quiet_analysis_logs: bool = True


    ):
        self.project_root = Path(project_root)
        self.runner_file = Path(runner_file)

        # 原来 input_file 必传，现在为了批量模式允许 None
        self.input_file = Path(input_file) if input_file else None

        self.output_dir = Path(output_dir)

        self.log_callback = log_callback
        self.finish_callback = finish_callback

        self.process = None
        self.temp_dir = None
        self.running = False
        self.thread = None

        # BMS分析参数
        self.enable_bms_analysis = bool(enable_bms_analysis)
        self.output_bms = bool(output_bms)
        self.bms_output_dir = Path(bms_output_dir) if bms_output_dir else None

        # 最终数据整理
        self.final_result_mapping_config_path = Path(final_result_mapping_config_path)
        self.enable_final_result_mapping = bool(final_result_mapping_config_path) is not None

        # 新增：批量处理参数
        self.input_dir = Path(input_dir) if input_dir else None
        self.batch_mode = bool(batch_mode)
        self.merge_json_results = bool(merge_json_results)
        self.save_individual_json = bool(save_individual_json)
        self.quiet_analysis_logs = bool(quiet_analysis_logs)


        # 内部固定参数
        self.speed_rate = 1.0
        self.cvt_flag = ""
        self.with_graph = False
        self.summary_only = True


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
    # validate
    # ------------------------------------------------------------------

    def validate(self):
        if not self.runner_file.exists() or not self.runner_file.is_file():
            raise FileNotFoundError(f"固定 Node runner 不存在:\n{self.runner_file}")

        # 新增：批量模式校验 input_dir
        if self.batch_mode:
            if self.input_dir is None:
                raise FileNotFoundError("批量模式未指定输入文件夹")

            if not self.input_dir.exists() or not self.input_dir.is_dir():
                raise FileNotFoundError(f"输入文件夹不存在:\n{self.input_dir}")

        # 原有：单文件模式校验 input_file
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

    def run_bms_convert_and_analysis(
        self,
        osu_file: Path,
        analyze_bms: bool,
    ) -> tuple[bool, dict[str, Any]]:
        return bms_utils.run_bms_convert_and_analysis(
            osu_file=osu_file,
            analyze_bms=analyze_bms,
            output_bms=self.output_bms,
            output_dir=self.output_dir,
            bms_output_dir=self.bms_output_dir,
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
        原有单文件逻辑保留。

        新增：
            - 如果 batch_mode=True，则进入 run_batch()
            - 单文件 / 单 .osz 模式也会根据 merge_json_results 决定是否输出 merged_results.json
        """

        # 新增：批量模式入口
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

        # 新增：收集最终保存的数据，用于合并 JSON
        merged_items: list[Any] = []

        for idx, osu_file in enumerate(osu_files, 1):
            if not self.running:
                self.log("[GUI] 任务已中断")
                break

            self.log("")
            self.log(f"[{idx}/{len(osu_files)}] {osu_file.name}")
            self.log("-" * 70)

            # 1. 执行 mixed 分析/提取 mixed JSON 数据（不保存）
            ok, data = self.run_node_process(osu_file)

            if ok:
                completed += 1

            # 2. 决定是否执行 BMS 分析
            should_convert_bms, should_analyze_bms, reason = self.should_run_bms_after_mixed(data)

            bms_payload = None

            if should_convert_bms:
                self.log("")
                self.log(f"[BMS] 触发 BMS 转换: {reason}")

                bms_ok, bms_payload = self.run_bms_convert_and_analysis(
                    osu_file=osu_file,
                    analyze_bms=should_analyze_bms,
                )

                if bms_ok:
                    bms_completed += 1
            else:
                self.log("")
                self.log(f"[BMS] 跳过 BMS 转换/分析: {reason}")
                bms_payload = bms_utils.build_empty_bms_payload(
                    output_bms=self.output_bms,
                    reason=reason,
                )

            # 4. 合并保存 JSON（mixed + BMS）
            saved_item = self.save_json_and_print_summary(
                data=data,
                osu_file=osu_file,
                bms_payload=bms_payload,

                # 批处理时默认不保存单独 JSON
                save_file=self.save_individual_json,

                # 批处理时不打印 summaryText
                print_summary=not self.quiet_analysis_logs,

                # 批处理时不打印每个文件的 JSON 保存日志和 route.mode
                log_result=self.save_individual_json and not self.quiet_analysis_logs,
)


            # 新增：收集最终输出的数据
            if saved_item is not None:
                merged_items.append(saved_item)

        self.log("")
        self.log("=" * 70)

        if self.running:
            self.log(f"[GUI] 全部完成，共 mixed 分析 {completed}/{len(osu_files)} 个谱面")
            self.log(f"[BMS] 共 BMS 转换 {bms_completed}/{len(osu_files)} 个谱面")
        else:
            self.log(f"[GUI] 已停止，已完成 mixed 分析 {completed}/{len(osu_files)} 个谱面")

        # 新增：单文件 / 单 .osz 模式也支持合并
        if self.merge_json_results and merged_items:
            self.save_merged_json_results(merged_items)

    # ------------------------------------------------------------------
    # 新增：batch flow
    # ------------------------------------------------------------------

    def run_batch(self):
        """
        批量处理文件夹下所有 .osu / .osz。

        逻辑：
            1. 扫描 input_dir 下所有 .osu / .osz；
            2. 如果是 .osu，直接分析；
            3. 如果是 .osz，沿用原有 extract_osz()，分析其中所有 .osu；
            4. 每个谱面照常输出单独 JSON；
            5. 最后额外输出 merged_results.json。
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

            # 关键：复用原有 collect_osu_files()
            # 原有函数依赖 self.input_file，所以这里临时切换。
            self.input_file = input_file

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
                self.log(f"[{file_idx}/{len(input_files)}] [{osu_idx}/{len(osu_files)}] {osu_file.name}")
                self.log("-" * 70)

                # 1. 执行 mixed 分析/提取 mixed JSON 数据（不保存）
                ok, data = self.run_node_process(osu_file)

                if ok:
                    total_mixed_count += 1

                # 2. 决定是否执行 BMS 分析
                should_convert_bms, should_analyze_bms, reason = self.should_run_bms_after_mixed(data)

                bms_payload = None

                if should_convert_bms:
                    self.log("")
                    self.log(f"[BMS] 触发 BMS 转换: {reason}")

                    bms_ok, bms_payload = self.run_bms_convert_and_analysis(
                        osu_file=osu_file,
                        analyze_bms=should_analyze_bms,
                    )

                    if bms_ok:
                        total_bms_count += 1
                else:
                    self.log("")
                    self.log(f"[BMS] 跳过 BMS 转换/分析: {reason}")
                    bms_payload = bms_utils.build_empty_bms_payload(
                        output_bms=self.output_bms,
                        reason=reason,
                    )

                # 3. 保存单个最终 JSON
                saved_item = self.save_json_and_print_summary(
                    data=data,
                    osu_file=osu_file,
                    bms_payload=bms_payload,
                    save_file=self.save_individual_json,
                    print_summary=not self.quiet_analysis_logs,
                    log_result=self.save_individual_json and not self.quiet_analysis_logs,
                )


                # 4. 收集合并数据
                if saved_item is not None:
                    merged_items.append(saved_item)

            # 每处理完一个 .osz，清理临时目录
            # 如果当前 input_file 是 .osu，这里没有副作用。
            self.cleanup_temp_dir()

        self.log("")
        self.log("=" * 70)

        if self.running:
            self.log(f"[GUI] 批量分析完成")
            self.log(f"[GUI] 共展开 .osu 谱面: {total_osu_count}")
            self.log(f"[GUI] 共 mixed 分析成功: {total_mixed_count}/{total_osu_count}")
            self.log(f"[BMS] 共 BMS 转换成功: {total_bms_count}/{total_osu_count}")
        else:
            self.log("[GUI] 批量分析已停止")
            self.log(f"[GUI] 已 mixed 分析成功: {total_mixed_count}/{total_osu_count}")
            self.log(f"[BMS] 已 BMS 转换成功: {total_bms_count}/{total_osu_count}")

        # 5. 输出合并 JSON
        if self.merge_json_results and merged_items:
            self.save_merged_json_results(merged_items)
        elif self.merge_json_results:
            self.log("[GUI] 没有可合并的 JSON 结果")

    def collect_batch_input_files(self) -> list[Path]:
        """
        新增：递归收集 input_dir 下所有 .osu / .osz 文件。

        默认递归处理子文件夹。
        如果你只想处理当前目录，把 rglob("*") 改成 iterdir()。
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

    def save_merged_json_results(self, items: list[Any]):
        """
        新增：保存所有最终 JSON 的合并结果。

        注意：
            这里合并的是 save_json_and_print_summary() 最终返回的数据，
            也就是已经经过：
                raw mixed
                + bms payload
                + prepare_final_result_source
                + apply_field_mapping
            后的最终输出对象。

        输出：
            output_dir / merged_results.json
        """

        if not items:
            self.log("[GUI] 没有可合并的数据")
            return None

        if self.batch_mode and self.input_dir is not None:
            merged_name = f"{self.input_dir.name}.json"
        else:
            merged_name = "merged_results.json"

        output_path = self.output_dir / merged_name


        try:
            json_utils.save_json_file(
                items,
                output_path,
            )

            self.log("")
            self.log("=" * 70)
            self.log(f"[GUI] 合并 JSON 已保存: {output_path}")
            self.log(f"[GUI] 合并结果数量: {len(items)}")
            self.log("=" * 70)

            return output_path

        except Exception as exc:
            self.log(f"[GUI ERROR] 保存合并 JSON 失败: {exc}")
            return None

    # ------------------------------------------------------------------
    # osu /osz
    # ------------------------------------------------------------------

    def collect_osu_files(self):
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

            # 这里只提取 JSON，不保存
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
        # 新增：如果 derived.keys != 7，则不加入合并 JSON
        skip_merge_output = False,

        # 新增：是否实际写入单独 JSON 文件
        save_file: bool = True,

        # 新增：是否打印 summaryText
        print_summary: bool = True,

        # 新增：是否打印保存结果、route.mode 等日志
        log_result: bool = True,
    ):

        """
        保存最终处理后的 JSON，并打印摘要。

        流程：
            mixed data + bms payload
                -> raw merged json
                -> final result processing
                -> final field mapping
                -> save final json

        Args:
            data: mixed 分析结果，已经是 dict，不再是 stdout 文本
            osu_file: 源 .osu 文件路径
            bms_payload: BMS 分析结果 dict
        """

        if not isinstance(data, dict) or not data:
            self.log("[GUI] mixed data 无效，未保存")
            return None

        # 避免直接修改原始 data 引用
        raw_data = deepcopy(data)

        # 取出 summary 文本
        # remove_summary_text 通常会返回:
        #   cleaned_data, summary_text
        cleaned_data, summary_text = json_utils.remove_summary_text(raw_data)

        if isinstance(cleaned_data, dict):
            raw_data = cleaned_data

        # 合并 BMS payload
        if isinstance(bms_payload, dict):
            raw_data["bms"] = bms_payload

        # route.mode 从 raw_data 里取
        # 因为最终映射后的 JSON 可能已经没有 route.mode
        route_mode = json_utils.get_route_mode(raw_data)

        # 生成 JSON 保存路径
        json_path = json_utils.make_unique_json_path(
            self.output_dir,
            osu_file,
            suffix=".mixed.json",
        )

        # 默认保存 raw_data
        # 如果后续处理和映射成功，则保存最终 data_to_save
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

                # 新增：最终结果映射前的分析处理入口
                # 在这里生成 derived / computed / normalized 之类的中间字段
                processed_data = prepare_final_result_source(
                    raw_data,
                    remove_none=False,
                )

                derived = processed_data.get("derived") if isinstance(processed_data, dict) else None
                derived_keys = derived.get("keys") if isinstance(derived, dict) else None
                try:
                    derived_keys_int = int(derived_keys)
                except Exception:
                    derived_keys_int = None

                if derived_keys_int != 7:
                    skip_merge_output = True

                # 字段映射使用 processed_data，而不是 raw_data
                data_to_save = apply_field_mapping(
                    processed_data,
                    config,
                )

        except Exception as exc:
            self.log(
                f"[GUI] 最终 JSON 字段处理/映射失败，将保存原始合并 JSON: {exc}"
            )
            data_to_save = raw_data

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

        if skip_merge_output:
            return None

        return data_to_save



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

            # 如果某些 BMS 工具输出不是 [BMS] 开头，也可以在这里继续加
            if normalized.startswith("bms "):
                return

        if self.log_callback:
            self.log_callback(text)

