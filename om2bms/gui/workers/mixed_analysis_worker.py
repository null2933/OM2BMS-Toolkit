import json
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from om2bms.pipeline.service import ConversionPipelineService
from om2bms.pipeline.types import ConversionOptions, DifficultyAnalysisMode
from om2bms.utils import json_utils,bms_utils


class MixedAnalysisWorker:
    def __init__(
        self,
        project_root: Path,
        runner_file: Path,
        input_file: Path,
        output_dir: Path,
        log_callback=None,
        finish_callback=None,
        enable_bms_analysis: bool = True,
        output_bms: bool = False,
        bms_output_dir: Path | None = None,
    ):
        self.project_root = Path(project_root)
        self.runner_file = Path(runner_file)
        self.input_file = Path(input_file)
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
        osu_files = self.collect_osu_files()

        self.log("")
        self.log("=" * 70)
        self.log("[GUI] 混合难度分析开始")
        self.log(f"[GUI] 找到 {len(osu_files)} 个 .osu 文件")
        self.log(f"[GUI] JSON 保存目录: {self.output_dir}")
        self.log("=" * 70)

        completed = 0
        bms_completed = 0

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
            self.save_json_and_print_summary(
                data=data,
                osu_file=osu_file,
                bms_payload=bms_payload,
            )


        self.log("")
        self.log("=" * 70)

        if self.running:
            self.log(f"[GUI] 全部完成，共 mixed 分析 {completed}/{len(osu_files)} 个谱面")
            self.log(f"[BMS] 共 BMS 转换 {bms_completed}/{len(osu_files)} 个谱面")
        else:
            self.log(f"[GUI] 已停止，已完成 mixed 分析 {completed}/{len(osu_files)} 个谱面")


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
    ):
        """
        保存合并后的 JSON（mixed + BMS）并打印摘要。

        Args:
            data: mixed 分析结果，已经是 dict，不再是 stdout 文本
            osu_file: 源 .osu 文件路径
            bms_payload: BMS 分析结果 dict
        """

        if not isinstance(data, dict) or not data:
            self.log("[GUI] mixed data 无效，未保存")
            return None

        # 取出 summary 文本
        _, summary_text = json_utils.remove_summary_text(data)

        # 生成 JSON 保存路径
        json_path = json_utils.make_unique_json_path(
            self.output_dir,
            osu_file,
            suffix=".mixed.json",
        )

        # 直接合并 BMS payload
        if isinstance(bms_payload, dict):
            data["bms"] = bms_payload

        try:
            json_utils.save_json_file(
                data,
                json_path,
            )

            if data.get("ok"):
                self.log(f"[GUI] JSON 已保存: {json_path}")
            else:
                self.log(f"[GUI] 分析失败 JSON 已保存: {json_path}")
                error_msg = data.get("error")
                if error_msg:
                    self.log(f"[GUI] 错误: {error_msg}")

        except Exception as exc:
            self.log(f"[GUI] 保存 JSON 失败: {exc}")
            return data

        if summary_text:
            self.log("")
            self.log(summary_text)

        route_mode = json_utils.get_route_mode(data)
        self.log(f"[GUI] route.mode: {route_mode!r}")

        return data



    # ------------------------------------------------------------------
    # callback
    # ------------------------------------------------------------------

    def log(self, text=""):
        if self.log_callback:
            self.log_callback(text)

