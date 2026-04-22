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


class Om2BmsGuiApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        configure_ui(self.root)
        self.root.title(APP_TITLE)
        self.root.geometry("1220x920")
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
        main = ttk.Frame(self.root, style="App.TFrame", padding=20)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(2, weight=2)

        header = ttk.Frame(main, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="现有转换流程保持不变；难度分析作为转换后的后处理步骤加入，支持关闭、单个目标和全部输出三种模式。",
            style="Subtitle.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        content = ttk.Frame(main, style="App.TFrame")
        content.grid(row=1, column=0, sticky="nsew", pady=(18, 14))
        content.columnconfigure(0, weight=5)
        content.columnconfigure(1, weight=7)
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content, style="App.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)

        right = ttk.Frame(content, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        mode_box = ttk.LabelFrame(left, text="转换模式", style="Card.TLabelframe")
        mode_box.grid(row=0, column=0, sticky="ew")
        mode_box.columnconfigure(0, weight=1)

        radio_specs = [
            ("single", "转换单个压缩包"),
            ("batch", "批量转换文件夹中的 .osz"),
            ("zip", "把 .zip 改名成 .osz"),
        ]
        for row, (value, text) in enumerate(radio_specs):
            button = ttk.Radiobutton(
                mode_box,
                text=text,
                variable=self.mode_var,
                value=value,
                command=self._sync_mode_widgets,
            )
            button.grid(row=row, column=0, sticky="w", pady=(0, 6))
            self.mode_buttons.append(button)

        ttk.Label(
            mode_box,
            textvariable=self.mode_hint_var,
            style="Hint.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))

        option_box = ttk.LabelFrame(
            left, text="转换选项", style="Card.TLabelframe")
        option_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        option_box.columnconfigure(0, weight=1)
        option_box.columnconfigure(1, weight=1)

        hitsound_check = ttk.Checkbutton(
            option_box, text="包含击打音效", variable=self.hitsound_var)
        hitsound_check.grid(row=0, column=0, sticky="w")

        bg_check = ttk.Checkbutton(
            option_box, text="处理背景图片", variable=self.bg_var)
        bg_check.grid(row=0, column=1, sticky="w", padx=(12, 0))

        ttk.Label(option_box, text="偏移量 (ms)").grid(
            row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Label(option_box, text="判定难度").grid(
            row=1, column=1, sticky="w", padx=(12, 0), pady=(12, 0))

        self.offset_entry = ttk.Entry(option_box, textvariable=self.offset_var)
        self.offset_entry.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        judge_frame = ttk.Frame(option_box)
        judge_frame.grid(row=2, column=1, sticky="ew",
                         padx=(12, 0), pady=(6, 0))
        judge_frame.columnconfigure(0, weight=1)
        judge_frame.columnconfigure(1, weight=1)
        for index, label in enumerate(JUDGE_OPTIONS.keys()):
            button = ttk.Radiobutton(
                judge_frame,
                text=label,
                variable=self.judge_var,
                value=label,
            )
            button.grid(row=index // 2, column=index %
                        2, sticky="w", padx=(0, 8), pady=(0, 4))
            self.judge_buttons.append(button)

        analysis_box = ttk.LabelFrame(
            left, text="难度分析", style="Card.TLabelframe")
        analysis_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        analysis_box.columnconfigure(0, weight=1)
        analysis_box.columnconfigure(1, weight=1)

        ttk.Label(analysis_box, text="分析模式").grid(row=0, column=0, sticky="w")
        ttk.Label(analysis_box, text="目标选择器").grid(
            row=0, column=1, sticky="w", padx=(12, 0))

        analysis_mode_frame = ttk.Frame(analysis_box)
        analysis_mode_frame.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        analysis_mode_frame.columnconfigure(0, weight=1)
        for row, (mode_value, mode_label) in enumerate(ANALYSIS_MODE_LABELS.items()):
            button = ttk.Radiobutton(
                analysis_mode_frame,
                text=mode_label,
                variable=self.analysis_mode_var,
                value=mode_value,
                command=self._sync_mode_widgets,
            )
            button.grid(row=row, column=0, sticky="w", pady=(0, 4))
            self.analysis_mode_buttons.append(button)

        self.analysis_target_entry = ttk.Entry(
            analysis_box, textvariable=self.analysis_target_var)
        self.analysis_target_entry.grid(
            row=1, column=1, sticky="ew", padx=(12, 0), pady=(6, 0))

        ttk.Label(
            analysis_box,
            text="单目标模式支持 chartId、输出文件名、难度标签、源文件名或序号，不会硬编码为 Easy/Normal/Hard。",
            style="Hint.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        tips_box = ttk.LabelFrame(left, text="使用提示", style="Card.TLabelframe")
        tips_box.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        tips_box.columnconfigure(0, weight=1)

        tips_text = (
            "1. 单个模式适合处理一个 .osz / .zip；批量模式会遍历文件夹中的全部 .osz；ZIP 模式只改扩展名，不做转换。\n"
            "2. 难度分析对象始终是最终导出的 BMS 文件，不是原始 OSZ；关闭分析时只做转换。\n"
            "3. 单目标模式支持 chartId、输出文件名、难度标签、源文件名或序号；日志里会打印 chartId，方便下次精确指定。\n"
            "4. 批量模式会对每个压缩包分别执行同样的分析策略；单个谱面分析失败不会回滚其他已输出结果。\n"
            "5. 导出表格会保留当前这轮转换结果的完整字段，只是把分析核心列排在前面，方便查看。\n"
            "6. 打开输出文件夹可直接查看产物；浏览源码会打开本地工程，导出源码包会打包当前源码目录，方便直接发人或整理到 GitHub。"
        )
        ttk.Label(
            tips_box,
            text=tips_text,
            justify="left",
            wraplength=330,
        ).grid(row=0, column=0, sticky="w")

        path_box = ttk.LabelFrame(right, text="文件路径", style="Card.TLabelframe")
        path_box.grid(row=0, column=0, sticky="ew")
        path_box.columnconfigure(0, weight=1)

        ttk.Label(path_box, textvariable=self.input_label_var).grid(
            row=0, column=0, sticky="w")

        input_entry = ttk.Entry(path_box, textvariable=self.input_var)
        input_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        input_button_row = ttk.Frame(path_box)
        input_button_row.grid(row=2, column=0, sticky="w", pady=(10, 0))

        self.input_file_button = ttk.Button(
            input_button_row,
            text="选择文件",
            style="Secondary.TButton",
            command=self._select_input_file,
        )
        self.input_file_button.grid(row=0, column=0, sticky="w")

        self.input_folder_button = ttk.Button(
            input_button_row,
            text="选择文件夹",
            style="Secondary.TButton",
            command=self._select_input_folder,
        )
        self.input_folder_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(
            path_box,
            text="路径支持手动输入；窗口放大后输入框会自动变宽。",
            style="Hint.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))

        self.output_frame = ttk.Frame(path_box)
        self.output_frame.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        self.output_frame.columnconfigure(0, weight=1)

        ttk.Label(self.output_frame, text="输出文件夹").grid(
            row=0, column=0, sticky="w")
        output_entry = ttk.Entry(
            self.output_frame, textvariable=self.output_var)
        output_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.output_button = ttk.Button(
            self.output_frame,
            text="选择输出文件夹",
            style="Secondary.TButton",
            command=self._select_output,
        )
        self.output_button.grid(row=2, column=0, sticky="w", pady=(10, 0))

        action_box = ttk.LabelFrame(
            right, text="开始执行", style="Card.TLabelframe")
        action_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        action_box.columnconfigure(0, weight=1)

        button_row = ttk.Frame(action_box)
        button_row.grid(row=0, column=0, sticky="w")

        self.start_button = ttk.Button(
            button_row,
            text="开始执行",
            style="Primary.TButton",
            command=self._start,
        )
        self.start_button.grid(row=0, column=0, sticky="w")

        open_output_button = ttk.Button(
            button_row,
            text="打开输出文件夹",
            style="Secondary.TButton",
            command=self._open_output_folder,
        )
        open_output_button.grid(row=0, column=1, sticky="w", padx=(10, 0))

        # self.source_button = ttk.Button(
        #     button_row,
        #     text="浏览源码",
        #     style="Secondary.TButton",
        #     command=self._browse_source_code,
        # )
        # self.source_button.grid(row=0, column=2, sticky="w", padx=(10, 0))

        # self.source_export_button = ttk.Button(
        #     button_row,
        #     text="导出源码包",
        #     style="Secondary.TButton",
        #     command=self._export_source_code,
        # )
        # self.source_export_button.grid(
        #     row=0, column=3, sticky="w", padx=(10, 0))

        self.export_button = ttk.Button(
            button_row,
            text="导出表格",
            style="Secondary.TButton",
            command=self._export_results_table,
            state="disabled",
        )
        self.export_button.grid(row=0, column=4, sticky="w", padx=(10, 0))

        clear_log_button = ttk.Button(
            button_row,
            text="清空日志",
            style="Secondary.TButton",
            command=self._clear_log,
        )
        clear_log_button.grid(row=0, column=5, sticky="w", padx=(10, 0))

        self.progress_bar = ttk.Progressbar(action_box, mode="indeterminate")
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(14, 0))

        status_frame = ttk.Frame(action_box)
        status_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        status_frame.columnconfigure(1, weight=1)

        ttk.Label(status_frame, text="当前状态").grid(row=0, column=0, sticky="w")
        ttk.Label(
            status_frame,
            textvariable=self.status_var,
            font=("Segoe UI Semibold", 11),
            foreground="#103f91",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        log_box = ttk.LabelFrame(main, text="运行日志", style="Card.TLabelframe")
        log_box.grid(row=2, column=0, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)

        self.log_box = ScrolledText(
            log_box,
            wrap="word",
            height=24,
            font=("Consolas", 11),
            padx=10,
            pady=10,
            relief="flat",
            borderwidth=0,
            background="#fbfcfe",
            foreground="#1c2838",
            undo=True,
        )
        self.log_box.grid(row=0, column=0, sticky="nsew")

        self.controls_to_toggle = [
            *self.mode_buttons,
            *self.judge_buttons,
            *self.analysis_mode_buttons,
            hitsound_check,
            bg_check,
            input_entry,
            output_entry,
            self.offset_entry,
            self.input_file_button,
            self.input_folder_button,
            self.output_button,
            self.analysis_target_entry
        ]

        self._append_log("图形界面已加载。转换主流程已接入新的 pipeline，难度分析作为后处理步骤执行。")
        self._append_log("日志区域已经扩大；如果做过转换，可以导出结果表格，也可以直接打包当前源码。")
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

    # def _find_source_root(self) -> Path:
    #     candidates = [
    #         APP_DIR,
    #         APP_DIR.parent,
    #         Path.cwd(),
    #     ]
    #     for candidate in candidates:
    #         if (candidate / "om2bms").is_dir() and (candidate / "om2bms_osz_gui.py").exists():
    #             return candidate
    #     return APP_DIR

    # def _browse_source_code(self) -> None:
    #     source_root = self._find_source_root()
    #     if not source_root.exists():
    #         messagebox.showwarning(APP_TITLE, "未找到可浏览的源码目录。")
    #         return

    #     os.startfile(str(source_root))
    #     self._append_log(f"已打开源码目录：{source_root}")

    # def _should_skip_source_export_path(self, relative_path: Path) -> bool:
    #     file_name = relative_path.name.lower()
    #     if relative_path.name in SOURCE_EXPORT_SKIP_FILES:
    #         return True
    #     if file_name.startswith("tmp_"):
    #         return True
    #     if relative_path.suffix.lower() in SOURCE_EXPORT_SKIP_SUFFIXES:
    #         return True

    #     parts = [part.lower() for part in relative_path.parts]
    #     if any(part in SOURCE_EXPORT_SKIP_DIRS for part in parts[:-1]):
    #         return True
    #     if any(
    #         part.startswith(("build", "dist", "tmp"))
    #         or part == "vendor"
    #         or part.endswith("_output")
    #         or part.startswith(("real_test", "gpu_test", "merge_test", "banner_fix_test"))
    #         for part in parts[:-1]
    #     ):
    #         return True
    #     return False

    # def _collect_source_export_files(self, source_root: Path, export_path: Path) -> list[Path]:
    #     collected: list[Path] = []
    #     export_resolved = export_path.resolve()
    #     for file_path in sorted(source_root.rglob("*")):
    #         if not file_path.is_file():
    #             continue

    #         resolved = file_path.resolve()
    #         if resolved == export_resolved:
    #             continue

    #         relative_path = file_path.relative_to(source_root)
    #         if self._should_skip_source_export_path(relative_path):
    #             continue

    #         collected.append(file_path)
    #     return collected

    # def _export_source_code(self) -> None:
    #     source_root = self._find_source_root()
    #     if not source_root.exists():
    #         messagebox.showwarning(APP_TITLE, "未找到可导出的源码目录。")
    #         return

    #     default_name = f"{source_root.name}-source-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    #     export_path = filedialog.asksaveasfilename(
    #         title="导出源码压缩包",
    #         defaultextension=".zip",
    #         initialdir=str(source_root.parent),
    #         initialfile=default_name,
    #         filetypes=[("ZIP 压缩包", "*.zip"), ("所有文件", "*.*")],
    #     )
    #     if not export_path:
    #         return

    #     export_file = Path(export_path)
    #     export_file.parent.mkdir(parents=True, exist_ok=True)

    #     source_files = self._collect_source_export_files(source_root, export_file)
    #     if not source_files:
    #         messagebox.showwarning(APP_TITLE, "没有找到可导出的源码文件。")
    #         return

    #     archive_root = source_root.name
    #     with zipfile.ZipFile(export_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    #         for file_path in source_files:
    #             relative_path = file_path.relative_to(source_root)
    #             archive_name = (Path(archive_root) / relative_path).as_posix()
    #             archive.write(file_path, archive_name)

    #     self._append_log(f"已导出源码压缩包：{export_file}（共 {len(source_files)} 个文件）")
    #     messagebox.showinfo(APP_TITLE, f"源码压缩包已导出到：\n{export_file}")

    # def _show_open_source_warning(self) -> None:
    #     if self.open_source_notice_shown:
    #         return

    #     self.open_source_notice_shown = True
    #     source_root = self._find_source_root()
    #     messagebox.showwarning(
    #         APP_TITLE,
    #         "开源前请确认：\n"
    #         "1. 检查第三方模型、图片、音频和依赖的许可证。\n"
    #         "2. 清理测试输出、dist/build、临时文件和无关资源。\n"
    #         "3. 确认源码与当前 EXE 行为一致后再发布到 GitHub。\n"
    #         "\n可用“浏览源码”查看本地工程，或用“导出源码包”直接打包当前源码：\n"
    #         f"{source_root}"
    #     )
    #     self._append_log("已显示开源前警告弹窗。")

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
