from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from queue import Queue, Empty
from pathlib import Path
from datetime import datetime

from om2bms.gui.constants import (
    APP_TITLE,
    EXPORT_COLUMNS,
    JUDGE_OPTIONS,
    ANALYSIS_MODE_LABELS,
    ANALYSIS_MODE_VALUE_BY_LABEL,
    DEFAULT_HITSOUND_ENABLED,
    DEFAULT_BG_ENABLED,
    DEFAULT_OFFSET_VALUE,
    DEFAULT_TN_VALUE,
    DEFAULT_JUDGE_VALUE,
)


from om2bms.gui.theme import configure_ui, apply_theme
from om2bms.gui.config_store import load_default_output_dir, save_default_output_dir
from om2bms.gui.exporters.csv_exporter import export_mapped_dict_rows_to_csv

from om2bms.gui.tabs.converter_tab import ConverterTab
from om2bms.gui.tabs.analyzer_tab import AnalyzerTab
from om2bms.gui.tabs.tablegen_tab import TableGenTab
from om2bms.gui.workers.conversion_worker import ConversionWorker

from om2bms.pipeline.types import ConversionOptions, ConversionResult, DifficultyAnalysisMode
# from om2bms.analysis.service import DifficultyAnalyzerService
# from om2bms.services.conversion_service import ConversionService


class Om2BmsGuiApp:
    def __init__(self) -> None:
        self.root = tk.Tk()

        configure_ui(self.root)

        self.root.title(APP_TITLE)
        self.root.geometry("1920x1080")
        self.root.minsize(1020, 760)
        self.root.resizable(True, True)
        self.root.configure(bg="#f3f6fb")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # ================= 变量 =================
        self.mode_var = tk.StringVar(value="single")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=load_default_output_dir())

        self.hitsound_var = tk.BooleanVar(value=DEFAULT_HITSOUND_ENABLED)
        self.bg_var = tk.BooleanVar(value=DEFAULT_BG_ENABLED)
        self.offset_var = tk.StringVar(value=DEFAULT_OFFSET_VALUE)
        self.judge_var = tk.StringVar(value=DEFAULT_JUDGE_VALUE)
        self.tn_var = tk.StringVar(value=DEFAULT_TN_VALUE)


        self.analysis_mode_var = tk.StringVar(value=DifficultyAnalysisMode.OFF.value)
        self.analysis_target_var = tk.StringVar()

        self.status_var = tk.StringVar(value="就绪")
        self.mode_hint_var = tk.StringVar(value="适合处理单个 .osz 或 .zip 谱面包。")
        self.input_label_var = tk.StringVar(value="输入 .osz 或 .zip 文件")

        # ================= 后台任务 =================
        self.queue: Queue[tuple[str, object]] = Queue()
        self.worker_thread: threading.Thread | None = None

        # ================= 导出 =================
        self.export_rows: list[dict[str, object]] = []
        self.export_default_name = "om2bms-results.csv"

        # ================= UI 引用 =================
        self.mode_buttons: list[ttk.Button] = []
        self.controls_to_toggle: list[tk.Widget] = []

        self.output_frame: ttk.Frame | None = None
        self.input_file_button: ttk.Button | None = None
        self.input_folder_button: ttk.Button | None = None
        self.output_button: ttk.Button | None = None

        self.offset_entry: ttk.Entry | None = None
        self.tn_entry: ttk.Entry | None = None

        self.judge_buttons: list[ttk.Button] = []
        self.analysis_mode_buttons: list[ttk.Button] = []
        self.analysis_target_entry: ttk.Entry | None = None

        self.start_button: ttk.Button | None = None
        self.export_button: ttk.Button | None = None
        self.progress_bar: ttk.Progressbar | None = None
        self.log_box: ScrolledText | None = None

        # ================= 主题 =================
        self.is_dark_theme = False
        self.theme_button_var = tk.StringVar(value="深色模式")
        self._theme_registered_widgets: list[tk.Widget] = []
        self._theme_registered_canvases: list[tk.Canvas] = []

        # ================= 构建 =================
        self._build_ui()
        self._sync_mode_widgets()

        self.root.after(150, self._process_queue)

    # ============================================================
    # UI 构建
    # ============================================================
    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame", padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        top_bar = ttk.Frame(container, style="App.TFrame")
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top_bar.columnconfigure(0, weight=1)
        top_bar.columnconfigure(1, weight=0)

        ttk.Label(
            top_bar,
            text=APP_TITLE,
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")

        ttk.Button(
            top_bar,
            textvariable=self.theme_button_var,
            command=self.toggle_theme,
        ).grid(row=0, column=1, sticky="e")

        notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew")

        tab_converter = ttk.Frame(notebook, style="App.TFrame")
        tab_analyzer = ttk.Frame(notebook, style="App.TFrame")
        tab_tablegen = ttk.Frame(notebook, style="App.TFrame")

        notebook.add(tab_converter, text="转谱工具")
        notebook.add(tab_analyzer, text="BMS难度分析")
        notebook.add(tab_tablegen, text="难易度生成工具")

        self.converter_tab = ConverterTab(self, tab_converter)
        self.analyzer_tab = AnalyzerTab(self, tab_analyzer)
        self.tablegen_tab = TableGenTab(self, tab_tablegen)

        self.apply_current_theme()
        self._sync_export_button()

    # ============================================================
    # 主题
    # ============================================================
    def register_theme_widget(self, widget) -> None:
        if widget is not None:
            self._theme_registered_widgets.append(widget)

    def register_theme_canvas(self, canvas) -> None:
        if canvas is not None:
            self._theme_registered_canvases.append(canvas)

    def apply_current_theme(self) -> None:
        apply_theme(self.root, dark=self.is_dark_theme)

        colors = getattr(self.root, "_theme_colors", {})

        text_bg = colors.get("text_bg", "#ffffff")
        text_fg = colors.get("text_fg", "#1f2328")
        select_bg = colors.get("select_bg", "#2f6feb")
        select_fg = colors.get("select_fg", "#ffffff")
        canvas_bg = colors.get("canvas_bg", "#f4f7fb")

        for widget in self._theme_registered_widgets:
            try:
                widget.configure(
                    background=text_bg,
                    foreground=text_fg,
                    insertbackground=text_fg,
                    selectbackground=select_bg,
                    selectforeground=select_fg,
                )
            except Exception:
                try:
                    widget.configure(
                        bg=text_bg,
                        fg=text_fg,
                        insertbackground=text_fg,
                        selectbackground=select_bg,
                        selectforeground=select_fg,
                    )
                except Exception:
                    pass

        for canvas in self._theme_registered_canvases:
            try:
                canvas.configure(background=canvas_bg)
            except Exception:
                try:
                    canvas.configure(bg=canvas_bg)
                except Exception:
                    pass

        self.theme_button_var.set("浅色模式" if self.is_dark_theme else "深色模式")

    def toggle_theme(self) -> None:
        self.is_dark_theme = not self.is_dark_theme
        self.apply_current_theme()

    # ============================================================
    # 模式同步
    # ============================================================
    def _sync_mode_widgets(self) -> None:
        mode = self.mode_var.get()
        analysis_mode = self._get_analysis_mode_value()

        if mode == "single":
            self.input_label_var.set("输入 .osz 或 .zip 文件")
            self.mode_hint_var.set("适合处理单个 .osz 或 .zip 谱面包。")
            file_state = "normal"
            folder_state = "disabled"

        elif mode == "batch":
            self.input_label_var.set("输入包含 .osz 的文件夹")
            self.mode_hint_var.set("会遍历所选文件夹里的全部 .osz。")
            file_state = "disabled"
            folder_state = "normal"

        else:
            self.input_label_var.set("输入 .zip 文件或包含 .zip 的文件夹")
            self.mode_hint_var.set("只改扩展名，不触发 BMS 转换，也不会执行难度分析。")
            file_state = "normal"
            folder_state = "normal"

        if self.input_file_button is not None:
            self.input_file_button.configure(state=file_state)

        if self.input_folder_button is not None:
            self.input_folder_button.configure(state=folder_state)

        show_output = mode != "zip"

        if self.output_frame is not None:
            if show_output:
                self.output_frame.grid()
            else:
                self.output_frame.grid_remove()

        analysis_mode_state = "disabled" if mode == "zip" else "normal"

        for button in self.analysis_mode_buttons:
            button.configure(state=analysis_mode_state)

        if self.analysis_target_entry is not None:
            target_state = (
                "normal"
                if mode != "zip" and analysis_mode == DifficultyAnalysisMode.SINGLE.value
                else "disabled"
            )
            self.analysis_target_entry.configure(state=target_state)

    def _get_analysis_mode_value(self) -> str:
        raw_value = self.analysis_mode_var.get().strip()

        if raw_value in ANALYSIS_MODE_LABELS:
            return raw_value

        return ANALYSIS_MODE_VALUE_BY_LABEL.get(
            raw_value,
            DifficultyAnalysisMode.OFF.value,
        )

    # ============================================================
    # 文件选择
    # ============================================================
    def _select_input_file(self) -> None:
        mode = self.mode_var.get()

        if mode == "single":
            path = filedialog.askopenfilename(
                title="选择 .osz 或 .zip 文件",
                filetypes=[("谱面压缩包", "*.osz *.zip"), ("所有文件", "*.*")],
            )
        else:
            path = filedialog.askopenfilename(
                title="选择 .zip 文件",
                filetypes=[("ZIP 文件", "*.zip"), ("所有文件", "*.*")],
            )

        if path:
            self.input_var.set(path)

    def _select_input_folder(self) -> None:
        path = filedialog.askdirectory(title="选择文件夹")

        if path:
            self.input_var.set(path)

    def _select_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出文件夹")

        if path:
            self.output_var.set(path)
            save_default_output_dir(path)

    def _open_output_folder(self) -> None:
        path = self.output_var.get().strip()

        if not path:
            messagebox.showwarning(APP_TITLE, "请先选择输出文件夹。")
            return

        Path(path).mkdir(parents=True, exist_ok=True)

        try:
            os.startfile(path)
        except AttributeError:
            messagebox.showinfo(APP_TITLE, f"输出目录：\n{path}")

    # ============================================================
    # 日志
    # ============================================================
    def _clear_log(self) -> None:
        if self.log_box is None:
            return

        self.log_box.delete("1.0", "end")
        self._append_log("日志已清空。")

    def _append_log(self, text: str) -> None:
        if self.log_box is None:
            return

        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    # ============================================================
    # 导出
    # ============================================================
    def _sync_export_button(self) -> None:
        if self.export_button is None:
            return

        is_running = self.worker_thread is not None and self.worker_thread.is_alive()

        if is_running:
            self.export_button.configure(state="disabled")
        else:
            self.export_button.configure(state="normal" if self.export_rows else "disabled")

    def _build_export_default_name(self, input_path: str) -> str:
        candidate = Path(input_path)

        if candidate.is_file():
            base_name = candidate.stem
        else:
            base_name = candidate.name or "om2bms-results"

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{base_name}-conversion-results-{timestamp}.csv"

    def _export_results_table(self) -> None:
        if not self.export_rows:
            messagebox.showinfo(APP_TITLE, "当前还没有可导出的转换结果。")
            return

        export_path = filedialog.asksaveasfilename(
            title="导出结果表格",
            defaultextension=".csv",
            initialfile=self.export_default_name,
            filetypes=[("CSV 表格", "*.csv"), ("所有文件", "*.*")],
        )

        if not export_path:
            return

        try:
            export_mapped_dict_rows_to_csv(
                export_path,
                self.export_rows,
                EXPORT_COLUMNS,
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"导出失败：{exc}")
            return

        self._append_log(f"已导出结果表格：{export_path}")
        messagebox.showinfo(APP_TITLE, f"结果表格已导出到：\n{export_path}")

    # ============================================================
    # 运行状态
    # ============================================================
    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"

        for widget in self.controls_to_toggle:
            try:
                widget.configure(state=state)
            except Exception:
                pass

        if self.start_button is not None:
            self.start_button.configure(state="disabled" if running else "normal")

        if self.progress_bar is not None:
            if running:
                self.progress_bar.start(12)
            else:
                self.progress_bar.stop()

        if not running:
            self._sync_mode_widgets()

        self._sync_export_button()

    # ============================================================
    # 构建转换选项
    # ============================================================
    def _build_conversion_options(self) -> ConversionOptions:
        mode = self.mode_var.get()

        try:
            offset = int(self.offset_var.get().strip())
        except ValueError as exc:
            raise ValueError("偏移量必须是整数。") from exc

        analysis_mode = self._get_analysis_mode_value()
        enable_analysis = mode != "zip" and analysis_mode != DifficultyAnalysisMode.OFF.value

        try:
            tn_value = float(self.tn_var.get())
        except (ValueError, tk.TclError) as exc:
            raise ValueError("T/N 必须是数字。") from exc

        if not (0 < tn_value < 10):
            raise ValueError("T/N 值必须大于 0 且小于 10。")

        judge_label = self.judge_var.get().strip()

        if judge_label not in JUDGE_OPTIONS:
            raise ValueError("请从下拉框选择有效的判定难度。")

        return ConversionOptions(
            hitsound=self.hitsound_var.get(),
            bg=self.bg_var.get(),
            offset=offset,
            judge=JUDGE_OPTIONS[judge_label],
            enable_difficulty_analysis=enable_analysis,
            difficulty_analysis_mode=analysis_mode,
            difficulty_target_id=self.analysis_target_var.get().strip() or None,
            tn_value=tn_value,
        )

    def _validate_inputs(self) -> tuple[str, str, ConversionOptions]:
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        mode = self.mode_var.get()

        if not input_path:
            raise ValueError("请先选择输入文件或文件夹。")

        if not Path(input_path).exists():
            raise FileNotFoundError(f"输入路径不存在：{input_path}")

        options = self._build_conversion_options()

        if mode != "zip":
            if not output_path:
                raise ValueError("请先选择输出文件夹。")

            Path(output_path).mkdir(parents=True, exist_ok=True)
            save_default_output_dir(output_path)

        if (
            options.enable_difficulty_analysis
            and options.resolved_analysis_mode() == DifficultyAnalysisMode.SINGLE
        ):
            if not options.difficulty_target_id:
                raise ValueError("单目标分析模式必须填写目标选择器。")

        return input_path, output_path, options

    # ============================================================
    # 开始任务
    # ============================================================
    def _start(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return

        try:
            input_path, output_path, options = self._validate_inputs()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self._set_running(True)

        self.export_rows.clear()
        self.export_default_name = self._build_export_default_name(input_path)
        self._sync_export_button()

        self.status_var.set("运行中")

        self._append_log("")
        self._append_log(f"模式：{self.mode_var.get()}")
        self._append_log(f"输入：{input_path}")

        if self.mode_var.get() != "zip":
            self._append_log(f"输出根目录：{output_path}")
            self._append_log(
                f"分析模式：{ANALYSIS_MODE_LABELS[options.resolved_analysis_mode().value]}"
            )

            if options.difficulty_target_id:
                self._append_log(f"分析目标选择器：{options.difficulty_target_id}")

        worker = ConversionWorker(self.queue)

        self.worker_thread = threading.Thread(
            target=worker.run,
            args=(self.mode_var.get(), input_path, output_path, options),
            daemon=True,
        )

        self.worker_thread.start()

    # ============================================================
    # 结果记录
    # ============================================================
    def _record_export_rows(self, result: ConversionResult) -> None:
        archive_name = Path(result.output_directory).name if result.output_directory else ""

        if not result.charts:
            self.export_rows.append(
                {
                    "archive_name": archive_name,
                    "output_directory": result.output_directory or "",
                    "chart_id": "",
                    "chart_index": "",
                    "source_chart_name": "",
                    "source_osu_path": "",
                    "source_difficulty_label": "",
                    "output_file_name": "",
                    "output_path": "",
                    "conversion_status": "failed",
                    "conversion_error": result.conversion_error or "",
                    "analysis_enabled": "FALSE",
                    "analysis_status": "",
                    "estimated_difficulty": "",
                    "raw_score": "",
                    "difficulty_table": "",
                    "analysis_label": "",
                    "difficulty_display": "",
                    "analysis_source": "",
                    "runtime_provider": "",
                    "analysis_error": "",
                    "analysis_selection_error": result.analysis_error or "",
                }
            )
            return

        for chart in result.charts:
            analysis = result.analysis_result_for(chart.chart_id)

            self.export_rows.append(
                {
                    "archive_name": archive_name,
                    "output_directory": result.output_directory or "",
                    "chart_id": chart.chart_id,
                    "chart_index": chart.chart_index,
                    "source_chart_name": chart.source_chart_name,
                    "source_osu_path": chart.source_osu_path,
                    "source_difficulty_label": chart.difficulty_label or "",
                    "output_file_name": chart.output_file_name or "",
                    "output_path": chart.output_path or "",
                    "conversion_status": chart.conversion_status,
                    "conversion_error": chart.conversion_error or result.conversion_error or "",
                    "analysis_enabled": (
                        "TRUE" if analysis is not None and analysis.enabled else "FALSE"
                    ),
                    "analysis_status": analysis.status if analysis is not None else "",
                    "estimated_difficulty": (
                        f"{analysis.estimated_difficulty:.4f}"
                        if analysis is not None and analysis.estimated_difficulty is not None
                        else ""
                    ),
                    "raw_score": (
                        f"{analysis.raw_score:.6f}"
                        if analysis is not None and analysis.raw_score is not None
                        else ""
                    ),
                    "difficulty_table": analysis.difficulty_table if analysis is not None else "",
                    "analysis_label": analysis.difficulty_label if analysis is not None else "",
                    "difficulty_display": analysis.difficulty_display if analysis is not None else "",
                    "analysis_source": analysis.analysis_source if analysis is not None else "",
                    "runtime_provider": analysis.runtime_provider if analysis is not None else "",
                    "analysis_error": analysis.error if analysis is not None else "",
                    "analysis_selection_error": result.analysis_error or "",
                }
            )

    def _log_conversion_result(self, result: ConversionResult) -> None:
        if result.output_directory:
            self._append_log(f"输出目录：{result.output_directory}")

        if result.conversion_error:
            self._append_log(f"转换错误：{result.conversion_error}")

        if result.analysis_error:
            self._append_log(f"分析选择错误：{result.analysis_error}")

        if result.charts:
            self._append_log("转换产物：")

            for chart in result.charts:
                if chart.conversion_status == "success":
                    self._append_log(
                        f"  [{chart.chart_index}] chartId={chart.chart_id} "
                        f"file={chart.output_file_name or '-'} "
                        f"difficulty={chart.difficulty_label or '-'}"
                    )
                else:
                    self._append_log(
                        f"  [{chart.chart_index}] FAILED source={chart.source_chart_name} "
                        f"error={chart.conversion_error}"
                    )

        if result.analysis_results:
            self._append_log("分析结果：")

            for analysis in result.analysis_results:
                if analysis.status == "success":
                    self._append_log(
                        f"  chartId={analysis.chart_id} status=success "
                        f"label={analysis.difficulty_label} "
                        f"provider={analysis.runtime_provider or '-'} "
                        f"display={analysis.difficulty_display} "
                        f"raw={analysis.raw_score:.6f}"
                    )
                elif analysis.status == "failed":
                    self._append_log(
                        f"  chartId={analysis.chart_id} status=failed error={analysis.error}"
                    )
                else:
                    self._append_log(
                        f"  chartId={analysis.chart_id} status=skipped"
                    )

    # ============================================================
    # 队列处理
    # ============================================================
    def _process_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()

                if kind == "log":
                    self._append_log(str(payload))

                elif kind == "status":
                    self.status_var.set(str(payload))

                elif kind == "result":
                    self._record_export_rows(payload)
                    self._log_conversion_result(payload)
                    self._sync_export_button()

                elif kind == "done":
                    self._append_log(str(payload))
                    self.status_var.set("完成")
                    self._set_running(False)
                    messagebox.showinfo(APP_TITLE, str(payload))

                elif kind == "error":
                    self._append_log(f"错误：{payload}")
                    self.status_var.set("出错")
                    self._set_running(False)
                    messagebox.showerror(APP_TITLE, str(payload))

        except Empty:
            pass

        finally:
            self.root.after(150, self._process_queue)

    def run(self) -> None:
        self.root.mainloop()
