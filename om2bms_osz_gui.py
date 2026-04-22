from __future__ import annotations

import csv
import ctypes
import multiprocessing
import os
import sys
import threading
import zipfile

from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import tkinter as tk
import tkinter.font as tkfont

from om2bms.pipeline.types import ConversionOptions, ConversionResult, DifficultyAnalysisMode
from om2bms.analysis.service import DifficultyAnalyzerService
from om2bms.services.conversion_service import ConversionService


APP_TITLE = "OSZ2BMS"
DEFAULT_OUTPUT_DIRNAME = "output"
JUDGE_OPTIONS = {
    "EASY": 3,
    "NORMAL": 2,
    "HARD": 1,
    "VERYHARD": 0,
}
ANALYSIS_MODE_LABELS = {
    DifficultyAnalysisMode.OFF.value: "关闭分析",
    DifficultyAnalysisMode.SINGLE.value: "分析单个输出",
    DifficultyAnalysisMode.ALL.value: "分析全部输出",
}
ANALYSIS_MODE_VALUE_BY_LABEL = {
    label: value for value, label in ANALYSIS_MODE_LABELS.items()}
SOURCE_EXPORT_SKIP_DIRS = {"__pycache__",
                           ".git", ".pytest_cache", ".mypy_cache"}
SOURCE_EXPORT_SKIP_FILES = {"default_outdir.txt", "default_outdir.ini"}
SOURCE_EXPORT_SKIP_SUFFIXES = {".exe", ".osz", ".pyc", ".pyo", ".zip"}
EXPORT_COLUMNS = [
    ("difficulty_table", "难度表"),
    ("analysis_label", "分析标签"),
    ("difficulty_display", "难度显示"),
    ("estimated_difficulty", "估计难度"),
    ("raw_score", "原始分数"),
    ("source_difficulty_label", "源难度标签"),
    ("output_file_name", "输出文件"),
    ("output_path", "输出路径"),
    ("conversion_status", "转换状态"),
    ("conversion_error", "转换错误"),
    ("analysis_enabled", "分析启用"),
    ("analysis_status", "分析状态"),
    ("analysis_source", "分析来源"),
    ("runtime_provider", "推理后端"),
    ("analysis_error", "分析错误"),
    ("analysis_selection_error", "分析选择错误"),
    ("archive_name", "压缩包"),
    ("output_directory", "输出目录"),
    ("chart_id", "chartId"),
    ("chart_index", "序号"),
    ("source_chart_name", "源谱面文件"),
    ("source_osu_path", "源谱面相对路径"),
]


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
CONFIG_FILE = APP_DIR / "default_outdir.txt"


def load_default_output_dir() -> str:
    if CONFIG_FILE.exists():
        saved = CONFIG_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    default_dir = APP_DIR / DEFAULT_OUTPUT_DIRNAME
    default_dir.mkdir(parents=True, exist_ok=True)
    return str(default_dir)


def save_default_output_dir(path: str) -> None:
    CONFIG_FILE.write_text(path.strip(), encoding="utf-8")


def configure_ui(root: tk.Tk) -> None:
    style = ttk.Style(root)
    for theme_name in ("vista", "xpnative", "clam"):
        if theme_name in style.theme_names():
            style.theme_use(theme_name)
            break

    default_font = tkfont.nametofont("TkDefaultFont")
    text_font = tkfont.nametofont("TkTextFont")
    fixed_font = tkfont.nametofont("TkFixedFont")
    heading_font = tkfont.nametofont("TkHeadingFont")

    default_font.configure(family="Segoe UI", size=10)
    text_font.configure(family="Segoe UI", size=10)
    fixed_font.configure(family="Consolas", size=10)
    heading_font.configure(family="Segoe UI Semibold", size=11)

    root.option_add("*Font", default_font)
    root.option_add("*TCombobox*Listbox.font", "Segoe UI 10")

    style.configure("App.TFrame", background="#f3f6fb")
    style.configure("Card.TLabelframe", padding=14)
    style.configure("Card.TLabelframe.Label", font=("Segoe UI Semibold", 11))
    style.configure("Title.TLabel", background="#f3f6fb",
                    foreground="#10233d", font=("Segoe UI Semibold", 21))
    style.configure("Subtitle.TLabel", background="#f3f6fb",
                    foreground="#51627c", font=("Segoe UI", 10))
    style.configure("Hint.TLabel", foreground="#5c6b82", font=("Segoe UI", 9))
    style.configure("Primary.TButton", padding=(
        18, 10), font=("Segoe UI Semibold", 11))
    style.configure("Secondary.TButton", padding=(12, 8))


def rename_zip_to_osz(path: str) -> int:
    source = Path(path)
    if source.is_file():
        if source.suffix.lower() != ".zip":
            return 0
        target = source.with_suffix(".osz")
        if target.exists():
            return 0
        source.rename(target)
        return 1

    if not source.is_dir():
        return 0

    count = 0
    for zip_file in sorted(source.glob("*.zip")):
        target = zip_file.with_suffix(".osz")
        if target.exists():
            continue
        zip_file.rename(target)
        count += 1
    return count


class ConverterTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent
        self.parent.update_idletasks()
        self._build()

    def _build(self):
        app = self.app

        # ================= 根容器 =================
        main = ttk.Frame(self.parent, style="App.TFrame", padding=20)
        main.grid(row=0, column=0, sticky="nsew")

        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(0, weight=1)

        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        # ================= HEADER =================
        header = ttk.Frame(main, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        ttk.Label(
            header,
            text="现有转换流程保持不变；难度分析作为转换后的后处理步骤加入，支持关闭、单个目标和全部输出三种模式。",
            style="Subtitle.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        # ================= 左右分割 =================
        content = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        content.grid(row=1, column=0, sticky="nsew", pady=(18, 14))

        # ================= 左侧 =================
        left = ttk.Frame(content, style="App.TFrame")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        content.add(left, weight=3)

        # ================= 右侧 =================
        right_paned = ttk.PanedWindow(content, orient=tk.VERTICAL)
        content.add(right_paned, weight=7)

        # ================= 右上 =================
        right_top = ttk.Frame(right_paned, style="App.TFrame")
        right_top.columnconfigure(0, weight=1)
        right_paned.add(right_top, weight=4)

        # ================= 右下 =================
        right_bottom = ttk.Frame(right_paned, style="App.TFrame")
        right_bottom.columnconfigure(0, weight=1)
        right_bottom.rowconfigure(0, weight=1)
        right_paned.add(right_bottom, weight=3)

        # ================= 左侧内容 =================
        # ===== 转换模式 =====
        mode_box = ttk.LabelFrame(left, text="转换模式", style="Card.TLabelframe")
        mode_box.grid(row=0, column=0, sticky="ew", padx=10, pady=(0, 8))

        for i, (v, t) in enumerate([
            ("single", "转换单个压缩包"),
            ("batch", "批量转换文件夹中的 .osz"),
            ("zip", "把 .zip 改名成 .osz"),
        ]):
            btn = ttk.Radiobutton(
                mode_box,
                text=t,
                variable=app.mode_var,
                value=v,
                command=app._sync_mode_widgets,
            )
            btn.grid(row=i, column=0, sticky="w", pady=2)
            app.mode_buttons.append(btn)

        ttk.Label(
            mode_box,
            textvariable=app.mode_hint_var,
            style="Hint.TLabel",
            wraplength=280,
        ).grid(row=3, column=0, sticky="w")

       # ===== 转换选项 =====
        option_box = ttk.LabelFrame(left, text="转换选项", style="Card.TLabelframe")
        option_box.grid(row=1, column=0, sticky="ew", padx=10, pady=8)
        option_box.columnconfigure(0, weight=1)
        option_box.columnconfigure(1, weight=1)
        option_box.columnconfigure(2, weight=1)  # ✅ 为T/N新增一列

        hitsound_check = ttk.Checkbutton(
            option_box, text="包含击打音效", variable=app.hitsound_var
        )
        hitsound_check.grid(row=0, column=0, sticky="w")

        bg_check = ttk.Checkbutton(option_box, text="处理背景图片", variable=app.bg_var)
        bg_check.grid(row=0, column=1, sticky="w")

        # ✅ 修改标签行，增加 T/N
        ttk.Label(option_box, text="偏移量(ms)").grid(row=1, column=0, sticky="w")
        ttk.Label(option_box, text="判定难度").grid(row=1, column=1, sticky="w")
        ttk.Label(option_box, text="T/N").grid(row=1, column=2, sticky="w")

        # 偏移量输入框
        app.offset_entry = ttk.Entry(option_box, textvariable=app.offset_var)
        app.offset_entry.grid(row=2, column=0, sticky="ew")

        # 判定难度按钮区域（原样保留）
        judge_frame = ttk.Frame(option_box)
        judge_frame.grid(row=2, column=1, sticky="ew")

        for i, label in enumerate(JUDGE_OPTIONS.keys()):
            btn = ttk.Radiobutton(
                judge_frame,
                text=label,
                variable=app.judge_var,
                value=label,
            )
            btn.grid(row=i // 2, column=i % 2, sticky="w")
            app.judge_buttons.append(btn)

        # ✅ 新增 T/N 输入框
        app.tn_var = tk.DoubleVar(value=0.2)  # 默认值 0.2
        app.tn_entry = ttk.Entry(option_box, textvariable=app.tn_var, width=8)
        app.tn_entry.grid(row=2, column=2, sticky="ew")


        for i, label in enumerate(JUDGE_OPTIONS.keys()):
            btn = ttk.Radiobutton(
                judge_frame,
                text=label,
                variable=app.judge_var,
                value=label,
            )
            btn.grid(row=i // 2, column=i % 2, sticky="w")
            app.judge_buttons.append(btn)

        # ===== 难度分析 =====
        analysis_box = ttk.LabelFrame(left, text="难度分析", style="Card.TLabelframe")
        analysis_box.grid(row=2, column=0, sticky="ew", padx=10, pady=8)
        analysis_box.columnconfigure(1, weight=1)

        ttk.Label(analysis_box, text="分析模式").grid(row=0, column=0)
        ttk.Label(analysis_box, text="目标").grid(row=0, column=1)

        frame = ttk.Frame(analysis_box)
        frame.grid(row=1, column=0, sticky="ew")

        for i, (v, t) in enumerate(ANALYSIS_MODE_LABELS.items()):
            btn = ttk.Radiobutton(
                frame,
                text=t,
                variable=app.analysis_mode_var,
                value=v,
                command=app._sync_mode_widgets,
            )
            btn.grid(row=i, column=0, sticky="w")
            app.analysis_mode_buttons.append(btn)

        app.analysis_target_entry = ttk.Entry(
            analysis_box, textvariable=app.analysis_target_var
        )
        app.analysis_target_entry.grid(row=1, column=1, sticky="ew")

        # ================= 右上：路径 =================
        path_box = ttk.LabelFrame(right_top, text="文件路径", style="Card.TLabelframe")
        path_box.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        path_box.columnconfigure(0, weight=1)

        ttk.Label(path_box, textvariable=app.input_label_var).grid(
            row=0, column=0, sticky="w"
        )

        input_entry = ttk.Entry(path_box, textvariable=app.input_var)
        input_entry.grid(row=1, column=0, sticky="ew", pady=2)

        btn_row = ttk.Frame(path_box)
        btn_row.grid(row=2, column=0, sticky="w", pady=2)

        app.input_file_button = ttk.Button(
            btn_row, text="选择文件", command=app._select_input_file
        )
        app.input_file_button.grid(row=0, column=0)

        app.input_folder_button = ttk.Button(
            btn_row, text="选择文件夹", command=app._select_input_folder
        )
        app.input_folder_button.grid(row=0, column=1)

        app.output_frame = ttk.Frame(path_box)
        app.output_frame.grid(row=3, column=0, sticky="ew", pady=4)
        app.output_frame.columnconfigure(0, weight=1)

        ttk.Label(app.output_frame, text="输出文件夹").grid(row=0, column=0, sticky="w")

        output_entry = ttk.Entry(app.output_frame, textvariable=app.output_var)
        output_entry.grid(row=1, column=0, sticky="ew", pady=2)

        app.output_button = ttk.Button(
            app.output_frame, text="选择输出文件夹", command=app._select_output
        )
        app.output_button.grid(row=2, column=0, sticky="w")

        # ===== 操作区 =====
        action_box = ttk.LabelFrame(right_top, text="开始执行")
        action_box.grid(row=1, column=0, sticky="ew", padx=10, pady=10)

        for i in range(4):
            action_box.columnconfigure(i, weight=1)

        app.start_button = ttk.Button(
            action_box, text="开始执行", command=app._start
        )
        app.start_button.grid(row=0, column=0, sticky="ew")

        ttk.Button(
            action_box,
            text="打开输出文件夹",
            command=app._open_output_folder,
        ).grid(row=0, column=1, sticky="ew", padx=5)

        app.export_button = ttk.Button(
            action_box,
            text="导出表格",
            command=app._export_results_table,
            state="disabled",
        )
        app.export_button.grid(row=0, column=2, sticky="ew", padx=5)

        ttk.Button(
            action_box,
            text="清空日志",
            command=app._clear_log,
        ).grid(row=0, column=3, sticky="ew", padx=5)

        app.progress_bar = ttk.Progressbar(action_box, mode="indeterminate")
        app.progress_bar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=5)

        # ================= 日志 =================
        log_box = ttk.LabelFrame(right_bottom, text="运行日志")
        log_box.grid(row=0, column=0, sticky="nsew", padx=10, pady=5)

        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)

        app.log_box = ScrolledText(
            log_box,
            wrap="word",
            font=("Consolas", 10),
            padx=8,
            pady=8,
        )
        app.log_box.grid(row=0, column=0, sticky="nsew")

        # ================= 初始分割位置（关键） =================
        def _init_pane():
            total = content.winfo_width()
            content.sashpos(0, int(total * 0.45)) #ui初始左右分割比例
            total_h = right_paned.winfo_height()
            right_paned.sashpos(0, int(total_h * 0.6)) 

        self.parent.after(100, _init_pane)

        # ================= 控件注册 =================
        app.controls_to_toggle = [
            *app.mode_buttons,
            *app.judge_buttons,
            *app.analysis_mode_buttons,
            hitsound_check,
            bg_check,
            input_entry,
            output_entry,
            app.offset_entry,
            app.input_file_button,
            app.input_folder_button,
            app.output_button,
            app.analysis_target_entry,
        ]

        app._append_log("Get Ready.")
        app._sync_export_button()

class AnalyzerTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent
        self.queue = Queue()
        self.worker_thread = None
        self.export_rows: list[dict] = []  # 用于导出
        self.export_default_name = "Analyzer_Results.csv"
        self._build()
        self.parent.after(150, self._process_queue)

    # ===========================
    # 构建界面
    # ===========================
    def _build(self):
        main = ttk.Frame(self.parent, padding=20)
        main.pack(fill="both", expand=True)

        # ===== 输入区 =====
        input_box = ttk.LabelFrame(main, text="输入")
        input_box.pack(fill="x", pady=5)

        self.input_var = tk.StringVar()
        ttk.Entry(input_box, textvariable=self.input_var).pack(
            fill="x", padx=5, pady=5
        )

        btn_row = ttk.Frame(input_box)
        btn_row.pack(fill="x", padx=5, pady=5)
        ttk.Button(btn_row, text="选择文件", command=self._select_file).pack(side="left")
        ttk.Button(btn_row, text="选择文件夹",
                   command=self._select_folder).pack(side="left", padx=5)

        # ===== 控制区 =====
        control_box = ttk.LabelFrame(main, text="控制")
        control_box.pack(fill="x", pady=5)

        self.start_btn = ttk.Button(control_box, text="开始分析", command=self._start)
        self.start_btn.pack(side="left", padx=5, pady=5)

        # ✅ 导出按钮（使用相同逻辑）
        self.export_btn = ttk.Button(
            control_box, text="导出分析表格", command=self._export_results_table)
        self.export_btn.pack(side="left", padx=5)

        self.progress = ttk.Progressbar(control_box, mode="indeterminate")
        self.progress.pack(fill="x", padx=5, pady=5)

       # ===== 结果表格 =====
        table_box = ttk.LabelFrame(main, text="结果")
        table_box.pack(fill="both", expand=True, pady=5)

        style = ttk.Style()
        style.configure("Treeview", rowheight=40)
        # style.configure("Treeview.Heading", font=("Microsoft YaHei", 10, "bold"))
        columns = ("Chart", "Difficulty", "Level")
        self.tree = ttk.Treeview(table_box, columns=columns, show="headings")

        for col in columns:
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, anchor="center", width=150)

        y_scrollbar = ttk.Scrollbar(table_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scrollbar.grid(row=0, column=1, sticky="ns")

        table_box.rowconfigure(0, weight=1)
        table_box.columnconfigure(0, weight=1)

        # ===== 日志 =====
        log_box = ttk.LabelFrame(main, text="日志")
        log_box.pack(fill="both", expand=True, pady=5)

        self.log = ScrolledText(log_box, height=8)
        self.log.pack(fill="both", expand=True)

    # ===========================
    # 文件选择
    # ===========================
    def _select_file(self):
        path = filedialog.askopenfilename(
            title="选择BMS文件",
            filetypes=[("BMS 文件", "*.bms *.bme *.bml *.pms"), ("所有文件", "*.*")]
        )
        if path:
            self.input_var.set(path)

    def _select_folder(self):
        path = filedialog.askdirectory(title="选择BMS文件夹")
        if path:
            self.input_var.set(path)

    # ===========================
    # 开始分析
    # ===========================
    def _start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        path = self.input_var.get().strip()
        if not path:
            messagebox.showinfo(APP_TITLE, "请选择文件或文件夹路径")
            return

        self.progress.start(10)
        self.start_btn.config(state="disabled")
        self.export_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.export_rows.clear()

        self.worker_thread = threading.Thread(
            target=self._run, args=(path,), daemon=True
        )
        self.worker_thread.start()

    # ===========================
    # 后台执行分析
    # ===========================
    def _run(self, path):
        service = DifficultyAnalyzerService()
        try:
            p = Path(path)
            if p.is_file():
                self._analyze_one(service, p)
            else:
                files = list(p.rglob("*.bms")) + list(p.rglob("*.bme")) \
                    + list(p.rglob("*.bml")) + list(p.rglob("*.pms"))
                if not files:
                    self.queue.put(("log", "未找到任何可分析文件"))
                    return

                total = len(files)
                for i, f in enumerate(files, 1):
                    self.queue.put(("log", f"[{i}/{total}] 分析 {f.name}..."))
                    self._analyze_one(service, f)

            self.queue.put(("done", "分析完成"))
        except Exception as e:
            self.queue.put(("error", str(e)))

    # ===========================
    # 单文件分析
    # ===========================
    def _analyze_one(self, service, path: Path):
        try:
            result = service.analyze_path(path)
            file_stem = Path(path).stem  # 自动去除 .bms / .bme / .bml / .pms 等后缀

            # 获取难度值与小数部分
            diff_value = float(result.estimated_difficulty or 0)
            diff_int = int(diff_value)
            diff_frac = diff_value - diff_int
            addon = ""
            if 0.25 <= diff_frac < 0.50:
                addon = "+"
            elif 0.50 <= diff_frac < 0.75:
                addon = "-"
            difficulty_display = f"{result.label}{addon}"
            row = {
                "Chart": file_stem,
                "Difficulty": f"{result.estimated_difficulty:.2f}",
                "Level": difficulty_display or "-",
                "raw": f"{result.raw_score:.4f}",
                "source": result.source,
            }

            # 添加到 Treeview
            self.queue.put(("result", row))
        except Exception as e:
            self.queue.put(("log", f"失败: {path.name}: {e}"))

    # ===========================
    # UI 队列更新
    # ===========================
    def _process_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()

                if kind == "log":
                    self._log(payload)

                elif kind == "result":
                    self.tree.insert("", "end", values=[
                        payload["Chart"],
                        payload["Difficulty"],
                        payload["Level"],
                        payload["raw"],
                        payload["source"],
                    ])
                    self.export_rows.append(payload)  # ✅ 保存导出数据

                elif kind == "done":
                    self._log(payload)
                    self.progress.stop()
                    self.start_btn.config(state="normal")
                    self.export_btn.config(state="normal")

                elif kind == "error":
                    self._log("错误：" + payload)
                    self.progress.stop()
                    self.start_btn.config(state="normal")
                    self.export_btn.config(state="normal")

        except Empty:
            pass

        self.parent.after(150, self._process_queue)

    # ===========================
    # 日志输出
    # ===========================
    def _log(self, text: str):
        self.log.insert("end", text + "\n")
        self.log.see("end")
    def _export_results_table(self) -> None:
        """导出当前 GUI 表格内容到 CSV（仅导出 row 中定义的 5 列）"""
        if not self.export_rows:
            messagebox.showinfo(APP_TITLE, "当前还没有可导出的分析结果。")
            return

        export_path = filedialog.asksaveasfilename(
            title="导出分析结果",
            defaultextension=".csv",
            initialfile=self.export_default_name,
            filetypes=[("CSV 表格", "*.csv"), ("所有文件", "*.*")]
        )
        if not export_path:
            return

        # ✅ 只包含 row 里的五个字段
        fieldnames = ["Chart", "Difficulty", "Level", "raw", "source"]

        try:
            with open(export_path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for row in self.export_rows:
                    # 按当前 row 字段导出
                    writer.writerow({k: row.get(k, "") for k in fieldnames})

            self._log(f"已导出结果表格：{export_path}")
            messagebox.showinfo(APP_TITLE, f"结果表格已导出到：\n{export_path}")

        except Exception as e:
            messagebox.showerror(APP_TITLE, f"导出失败：{e}")


class TableGenTab:
    def __init__(self, parent: ttk.Frame):
        ttk.Label(parent, text="待开发").pack()


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

        self.conversion_service = ConversionService()

        self.mode_var = tk.StringVar(value="single")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=load_default_output_dir())
        self.hitsound_var = tk.BooleanVar(value=True)
        self.bg_var = tk.BooleanVar(value=True)
        self.offset_var = tk.StringVar(value="0")
        self.judge_var = tk.StringVar(value="EASY")
        self.analysis_mode_var = tk.StringVar(
            value=DifficultyAnalysisMode.OFF.value)
        self.analysis_target_var = tk.StringVar()

        self.status_var = tk.StringVar(value="就绪")
        self.mode_hint_var = tk.StringVar(value="适合处理单个 .osz 或 .zip 谱面包。")
        self.input_label_var = tk.StringVar(value="输入 .osz 或 .zip 文件")

        self.queue: Queue[tuple[str, object]] = Queue()
        self.worker_thread: threading.Thread | None = None
        self.export_rows: list[dict[str, object]] = []
        self.export_default_name = "om2bms-results.csv"
        self.open_source_notice_shown = False

        self.mode_buttons: list[ttk.Radiobutton] = []
        self.controls_to_toggle: list[tk.Widget] = []

        self.output_frame: ttk.Frame | None = None
        self.input_file_button: ttk.Button | None = None
        self.input_folder_button: ttk.Button | None = None
        self.output_button: ttk.Button | None = None
        self.offset_entry: ttk.Entry | None = None
        self.judge_buttons: list[ttk.Radiobutton] = []
        self.analysis_mode_buttons: list[ttk.Radiobutton] = []
        self.analysis_target_entry: ttk.Entry | None = None
        self.start_button: ttk.Button | None = None
        self.source_button: ttk.Button | None = None
        self.source_export_button: ttk.Button | None = None
        self.export_button: ttk.Button | None = None
        self.progress_bar: ttk.Progressbar | None = None
        self.log_box: ScrolledText | None = None

        self._build_ui()
        self._sync_mode_widgets()
        self.root.after(150, self._process_queue)
        # self.root.after(350, self._show_open_source_warning)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame")
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(container)
        notebook.grid(row=0, column=0, sticky="nsew")

        tab_converter = ttk.Frame(notebook)
        tab_analyzer = ttk.Frame(notebook)
        tab_tablegen = ttk.Frame(notebook)

        notebook.add(tab_converter, text="CONVERTER")
        notebook.add(tab_analyzer, text="ANALYZER")
        notebook.add(tab_tablegen, text="TABLE GENERATOR")

        # ✅ 关键：把 UI 构建交给 class
        ConverterTab(self, tab_converter)
        AnalyzerTab(self, tab_analyzer)
        TableGenTab(tab_tablegen)

        self._sync_export_button()

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
            self.mode_hint_var.set("会遍历所选文件夹里的全部 .osz，并对每个压缩包分别执行转换和分析。")
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
            target_state = "normal" if (
                mode != "zip" and analysis_mode == DifficultyAnalysisMode.SINGLE.value) else "disabled"
            self.analysis_target_entry.configure(state=target_state)

    def _get_analysis_mode_value(self) -> str:
        raw_value = self.analysis_mode_var.get().strip()
        if raw_value in ANALYSIS_MODE_LABELS:
            return raw_value
        return ANALYSIS_MODE_VALUE_BY_LABEL.get(raw_value, DifficultyAnalysisMode.OFF.value)

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
        os.startfile(path)

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

    def _sync_export_button(self) -> None:
        if self.export_button is None:
            return

        is_running = self.worker_thread is not None and self.worker_thread.is_alive()
        if is_running:
            self.export_button.configure(state="disabled")
        else:
            self.export_button.configure(
                state="normal" if self.export_rows else "disabled")

    def _build_export_default_name(self, input_path: str) -> str:
        candidate = Path(input_path)
        if candidate.is_file():
            base_name = candidate.stem
        else:
            base_name = candidate.name or "om2bms-results"

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{base_name}-conversion-results-{timestamp}.csv"

    def _record_export_rows(self, result: ConversionResult) -> None:
        archive_name = Path(
            result.output_directory).name if result.output_directory else ""

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

        fieldnames = [label for _, label in EXPORT_COLUMNS]
        with open(export_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.export_rows:
                writer.writerow({label: row.get(key, "")
                                for key, label in EXPORT_COLUMNS})

        self._append_log(f"已导出结果表格：{export_path}")
        messagebox.showinfo(APP_TITLE, f"结果表格已导出到：\n{export_path}")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in self.controls_to_toggle:
            widget.configure(state=state)

        if self.start_button is not None:
            self.start_button.configure(
                state="disabled" if running else "normal")

        if self.progress_bar is not None:
            if running:
                self.progress_bar.start(12)
            else:
                self.progress_bar.stop()

        if not running:
            self._sync_mode_widgets()
        self._sync_export_button()

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
        except (ValueError, tk.TclError):
            raise ValueError("T/N 必须是数字。")

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

        if options.enable_difficulty_analysis and options.resolved_analysis_mode() == DifficultyAnalysisMode.SINGLE:
            if not options.difficulty_target_id:
                raise ValueError("单目标分析模式必须填写目标选择器。")

        return input_path, output_path, options

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
                f"分析模式：{ANALYSIS_MODE_LABELS[options.resolved_analysis_mode().value]}")
            if options.difficulty_target_id:
                self._append_log(f"分析目标选择器：{options.difficulty_target_id}")

        self.worker_thread = threading.Thread(
            target=self._run_task,
            args=(self.mode_var.get(), input_path, output_path, options),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_task(self, mode: str, input_path: str, output_path: str, options: ConversionOptions) -> None:
        try:
            
            if mode == "zip":
                count = rename_zip_to_osz(input_path)
                self.queue.put(("done", f"已把 {count} 个文件从 .zip 改名为 .osz。"))
                return

            if mode == "single":
                self.queue.put(("log", "开始处理单个压缩包"))
                result = self.conversion_service.convert_osz(
                    input_path, output_path, options)
                self.queue.put(("result", result))
                message = "转换完成。" if result.conversion_success else "转换失败。"
                self.queue.put(("done", message))
                return

            archives = sorted(Path(input_path).glob("*.osz"))
            if not archives:
                raise FileNotFoundError("所选文件夹中没有找到 .osz 文件。")

            total = len(archives)
            success_count = 0
            for index, archive in enumerate(archives, start=1):
                self.queue.put(("status", f"批量任务 {index}/{total}"))
                self.queue.put(("log", f"开始处理 {archive.name}"))
                result = self.conversion_service.convert_osz(
                    str(archive), output_path, options)
                self.queue.put(("result", result))
                if result.conversion_success:
                    success_count += 1

            self.queue.put(
                ("done", f"批量处理完成：成功 {success_count}/{total} 个压缩包。"))
        except Exception as exc:
            self.queue.put(("error", str(exc)))

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
                        f"  [{chart.chart_index}] FAILED source={chart.source_chart_name} error={chart.conversion_error}"
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
                        f"  chartId={analysis.chart_id} status=skipped")

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


def main() -> None:
    enable_high_dpi()
    multiprocessing.freeze_support()
    Om2BmsGuiApp().run()


if __name__ == "__main__":
    main()
