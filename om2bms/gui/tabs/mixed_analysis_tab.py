import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from om2bms.gui.workers.mixed_analysis_worker import MixedAnalysisWorker


PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parents[3]

RUNNER_FILE_DEFAULT = (
    PROJECT_ROOT_DEFAULT
    / "om_analysis"
    / "gui_mixed_runner.mjs"
)

DEFAULT_BMS_DIR_FILE = PROJECT_ROOT_DEFAULT / "default_bms_dir.txt"

FINAL_RESULT_MAPPING_CONFIG_DEFAULT = (
    PROJECT_ROOT_DEFAULT
    / "config"
    / "final_result_mapping.json"
)

TOGGLE_NORMAL_STYLE = "Choice.TButton"
TOGGLE_SELECTED_STYLE = "SelectedChoice.TButton"


def load_default_bms_dir():
    try:
        if DEFAULT_BMS_DIR_FILE.exists():
            text = DEFAULT_BMS_DIR_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception:
        pass

    return str(PROJECT_ROOT_DEFAULT / "bms_output")


class MixedAnalysisTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent

        self.worker = None

        # 输入模式：file / folder / info
        self.input_mode_var = tk.StringVar(value="file")

        # 单文件输入
        self.osu_file_var = tk.StringVar(value="")

        # 文件夹批量输入
        self.input_dir_var = tk.StringVar(value="")

        # 下载器 beatmap_info.json 输入
        self.info_file_var = tk.StringVar(value="")

        # JSON 输出目录
        self.output_dir_var = tk.StringVar(
            value=str(PROJECT_ROOT_DEFAULT / "json_results")
        )

        # 批量/单文件都可以开启合并
        self.merge_json_results_var = tk.BooleanVar(value=True)

        # BMS 选项
        self.enable_bms_analysis_var = tk.BooleanVar(value=True)
        self.output_bms_var = tk.BooleanVar(value=False)
        self.bms_output_dir_var = tk.StringVar(value=load_default_bms_dir())

        # 控件引用
        self.file_mode_button = None
        self.folder_mode_button = None
        self.info_mode_button = None

        self.input_file_entry = None
        self.input_file_button = None

        self.input_dir_entry = None
        self.input_dir_button = None

        self.info_file_entry = None
        self.info_file_button = None

        self.merge_json_results_button = None

        self.enable_bms_analysis_button = None
        self.output_bms_button = None
        self.bms_output_dir_entry = None
        self.bms_output_dir_button = None

        self.start_button = None
        self.stop_button = None
        self.clear_button = None
        self.log_text = None

        self.build_ui(parent)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def build_ui(self, parent):
        outer = ttk.Frame(parent, style="App.TFrame", padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        outer.columnconfigure(0, weight=0, minsize=420)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer, style="App.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(outer, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_config_panel(left)
        self._build_log_panel(right)

    def _build_config_panel(self, parent):
        ttk.Label(
            parent,
            text="混合难度分析",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        card = ttk.LabelFrame(
            parent,
            text="输入与输出",
            style="Card.TLabelframe",
            padding=12,
        )
        card.grid(row=1, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)

        row = 0
        pad_y = 7

        # --------------------------------------------------------------
        # 输入模式
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="输入模式:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        input_mode_frame = ttk.Frame(card, style="App.TFrame")
        input_mode_frame.grid(
            row=row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=pad_y,
        )

        self.file_mode_button = ttk.Button(
            input_mode_frame,
            text="单文件",
            command=lambda: self.set_input_mode("file"),
        )
        self.file_mode_button.pack(side=tk.LEFT, padx=(0, 6))

        self.folder_mode_button = ttk.Button(
            input_mode_frame,
            text="文件夹批量",
            command=lambda: self.set_input_mode("folder"),
        )
        self.folder_mode_button.pack(side=tk.LEFT, padx=(0, 6))

        self.info_mode_button = ttk.Button(
            input_mode_frame,
            text="下载器 JSON",
            command=lambda: self.set_input_mode("info"),
        )
        self.info_mode_button.pack(side=tk.LEFT, padx=(0, 6))

        row += 1

        # --------------------------------------------------------------
        # 单文件输入
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text=".osu / .osz 文件:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        self.input_file_entry = ttk.Entry(
            card,
            textvariable=self.osu_file_var,
        )
        self.input_file_entry.grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(0, 8),
            pady=pad_y,
        )

        self.input_file_button = ttk.Button(
            card,
            text="选择",
            command=self.choose_osu_file,
            width=10,
        )
        self.input_file_button.grid(row=row, column=2, sticky="e", pady=pad_y)

        row += 1

        # --------------------------------------------------------------
        # 文件夹批量输入
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="批量文件夹:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        self.input_dir_entry = ttk.Entry(
            card,
            textvariable=self.input_dir_var,
        )
        self.input_dir_entry.grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(0, 8),
            pady=pad_y,
        )

        self.input_dir_button = ttk.Button(
            card,
            text="选择",
            command=self.choose_input_dir,
            width=10,
        )
        self.input_dir_button.grid(row=row, column=2, sticky="e", pady=pad_y)

        row += 1

        # --------------------------------------------------------------
        # 下载器 beatmap_info.json 输入
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="下载器 JSON:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        self.info_file_entry = ttk.Entry(
            card,
            textvariable=self.info_file_var,
        )
        self.info_file_entry.grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(0, 8),
            pady=pad_y,
        )

        self.info_file_button = ttk.Button(
            card,
            text="选择",
            command=self.choose_info_file,
            width=10,
        )
        self.info_file_button.grid(row=row, column=2, sticky="e", pady=pad_y)

        row += 1

        # --------------------------------------------------------------
        # JSON 输出目录
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="JSON 保存目录:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        ttk.Entry(
            card,
            textvariable=self.output_dir_var,
        ).grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=pad_y)

        ttk.Button(
            card,
            text="选择",
            command=self.choose_output_dir,
            width=10,
        ).grid(row=row, column=2, sticky="e", pady=pad_y)

        row += 1

        # --------------------------------------------------------------
        # JSON 合并
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="JSON 合并:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        merge_frame = ttk.Frame(card, style="App.TFrame")
        merge_frame.grid(
            row=row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=pad_y,
        )

        self.merge_json_results_button = self._register_toggle_button(
            merge_frame,
            self.merge_json_results_var,
            "合并所有结果",
        )

        row += 1

        # --------------------------------------------------------------
        # BMS 开关
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="BMS 选项:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        bms_toggle_frame = ttk.Frame(card, style="App.TFrame")
        bms_toggle_frame.grid(
            row=row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=pad_y,
        )

        self.enable_bms_analysis_button = self._register_toggle_button(
            bms_toggle_frame,
            self.enable_bms_analysis_var,
            "BMS 分析",
            command=self.update_bms_controls_state,
        )

        self.output_bms_button = self._register_toggle_button(
            bms_toggle_frame,
            self.output_bms_var,
            "输出 BMS",
            command=self.update_bms_controls_state,
        )

        row += 1

        # --------------------------------------------------------------
        # BMS 输出目录
        # --------------------------------------------------------------
        ttk.Label(
            card,
            text="BMS 输出目录:",
            style="App.TLabel",
        ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=pad_y)

        self.bms_output_dir_entry = ttk.Entry(
            card,
            textvariable=self.bms_output_dir_var,
        )
        self.bms_output_dir_entry.grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(0, 8),
            pady=pad_y,
        )

        self.bms_output_dir_button = ttk.Button(
            card,
            text="选择",
            command=self.choose_bms_output_dir,
            width=10,
        )
        self.bms_output_dir_button.grid(
            row=row,
            column=2,
            sticky="e",
            pady=pad_y,
        )

        row += 1

        ttk.Label(
            card,
            text="分析器: om_analysis/gui_mixed_runner.mjs",
            style="Muted.TLabel",
        ).grid(
            row=row,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(10, 0),
        )

        self.update_input_mode_state()
        self.update_bms_controls_state()

        # --------------------------------------------------------------
        # 操作
        # --------------------------------------------------------------
        action_card = ttk.LabelFrame(
            parent,
            text="操作",
            style="Card.TLabelframe",
            padding=12,
        )
        action_card.grid(row=2, column=0, sticky="ew", pady=(12, 0))

        action_card.columnconfigure(0, weight=1)
        action_card.columnconfigure(1, weight=1)
        action_card.columnconfigure(2, weight=1)

        self.start_button = ttk.Button(
            action_card,
            text="开始分析",
            command=self.start_run,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.stop_button = ttk.Button(
            action_card,
            text="停止",
            command=self.stop_run,
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        self.clear_button = ttk.Button(
            action_card,
            text="清空日志",
            command=self.clear_log,
        )
        self.clear_button.grid(row=0, column=2, sticky="ew")

        # --------------------------------------------------------------
        # 说明
        # --------------------------------------------------------------
        info_card = ttk.LabelFrame(
            parent,
            text="说明",
            style="Card.TLabelframe",
            padding=12,
        )
        info_card.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        info_text = (
            "1. 支持选择单个 .osu 文件。\n"
            "2. 支持选择 .osz 包，会自动解压并分析其中所有 .osu。\n"
            "3. 支持选择文件夹，批量分析其中所有 .osu / .osz。\n"
            "4. 支持选择下载器 beatmap_info.json，根据其中 file_path 定位 .osz。\n"
            "5. 分析结果会保存为 JSON。\n"
            "6. 开启 JSON 合并后，会额外输出 merged_results.json。\n"
            "7. summaryText 只显示在日志中，不写入 JSON。\n"
            "8. Node runner 固定使用 om_analysis/gui_mixed_runner.mjs。\n"
            "9. 默认启用 BMS 分析，但默认不输出 BMS 文件。\n"
            "10. BMS 输出目录默认读取 default_bms_dir.txt。"
        )

        ttk.Label(
            info_card,
            text=info_text,
            justify="left",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")

    def _build_log_panel(self, parent):
        header = ttk.Frame(parent, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="分析日志",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")

        self.log_text = scrolledtext.ScrolledText(
            parent,
            wrap=tk.WORD,
            font=("Consolas", 10),
            height=20,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")

        if hasattr(self.app, "register_theme_text"):
            try:
                self.app.register_theme_text(self.log_text)
            except Exception:
                pass

    def _register_toggle_button(self, parent, variable, text, command=None):
        button = ttk.Button(
            parent,
            text=text,
            command=lambda: self._toggle_button_value(variable, command),
        )
        button.pack(side=tk.LEFT, padx=(0, 6))

        def refresh(*_):
            selected = bool(variable.get())
            button.configure(
                style=TOGGLE_SELECTED_STYLE if selected else TOGGLE_NORMAL_STYLE
            )

        variable.trace_add("write", refresh)
        refresh()

        return button

    def _toggle_button_value(self, variable, command=None):
        variable.set(not bool(variable.get()))

        if command:
            command()

    def set_input_mode(self, mode: str):
        if mode not in {"file", "folder", "info"}:
            return

        self.input_mode_var.set(mode)
        self.update_input_mode_state()

    def update_input_mode_state(self):
        mode = self.input_mode_var.get()

        file_enabled = mode == "file"
        folder_enabled = mode == "folder"
        info_enabled = mode == "info"

        try:
            if self.input_file_entry is not None:
                self.input_file_entry.configure(
                    state="normal" if file_enabled else "disabled"
                )

            if self.input_file_button is not None:
                self.input_file_button.configure(
                    state="normal" if file_enabled else "disabled"
                )

            if self.input_dir_entry is not None:
                self.input_dir_entry.configure(
                    state="normal" if folder_enabled else "disabled"
                )

            if self.input_dir_button is not None:
                self.input_dir_button.configure(
                    state="normal" if folder_enabled else "disabled"
                )

            if self.info_file_entry is not None:
                self.info_file_entry.configure(
                    state="normal" if info_enabled else "disabled"
                )

            if self.info_file_button is not None:
                self.info_file_button.configure(
                    state="normal" if info_enabled else "disabled"
                )

            if self.file_mode_button is not None:
                self.file_mode_button.configure(
                    style=TOGGLE_SELECTED_STYLE if file_enabled else TOGGLE_NORMAL_STYLE
                )

            if self.folder_mode_button is not None:
                self.folder_mode_button.configure(
                    style=TOGGLE_SELECTED_STYLE if folder_enabled else TOGGLE_NORMAL_STYLE
                )

            if self.info_mode_button is not None:
                self.info_mode_button.configure(
                    style=TOGGLE_SELECTED_STYLE if info_enabled else TOGGLE_NORMAL_STYLE
                )

        except Exception:
            pass

    def update_bms_controls_state(self):
        enable_bms = bool(self.enable_bms_analysis_var.get())
        output_bms = bool(self.output_bms_var.get())

        # 关闭 BMS 分析时，强制关闭输出 BMS
        if not enable_bms:
            if self.output_bms_var.get():
                self.output_bms_var.set(False)
            output_bms = False

        try:
            if self.output_bms_button is not None:
                self.output_bms_button.configure(
                    state="normal" if enable_bms else "disabled"
                )

            dir_state = "normal" if enable_bms and output_bms else "disabled"

            if self.bms_output_dir_entry is not None:
                self.bms_output_dir_entry.configure(state=dir_state)

            if self.bms_output_dir_button is not None:
                self.bms_output_dir_button.configure(state=dir_state)

        except Exception:
            pass

    # ------------------------------------------------------------------
    # file dialog
    # ------------------------------------------------------------------

    def choose_osu_file(self):
        path = filedialog.askopenfilename(
            title="选择 .osu 或 .osz 文件",
            filetypes=[
                ("osu files", "*.osu;*.osz"),
                ("osu beatmap", "*.osu"),
                ("osz package", "*.osz"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.osu_file_var.set(path)

    def choose_input_dir(self):
        path = filedialog.askdirectory(
            title="选择包含 .osu / .osz 的文件夹",
            initialdir=self.input_dir_var.get() or str(PROJECT_ROOT_DEFAULT),
        )

        if path:
            self.input_dir_var.set(path)

    def choose_info_file(self):
        path = filedialog.askopenfilename(
            title="选择下载器 beatmap_info.json",
            filetypes=[
                ("beatmap_info json", "beatmap_info.json"),
                ("json files", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.info_file_var.set(path)

    def choose_output_dir(self):
        path = filedialog.askdirectory(
            title="选择 JSON 保存目录",
            initialdir=self.output_dir_var.get() or str(PROJECT_ROOT_DEFAULT),
        )

        if path:
            self.output_dir_var.set(path)

    def choose_bms_output_dir(self):
        path = filedialog.askdirectory(
            title="选择 BMS 输出目录",
            initialdir=self.bms_output_dir_var.get() or str(PROJECT_ROOT_DEFAULT),
        )

        if path:
            self.bms_output_dir_var.set(path)

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def start_run(self):
        if self.worker and self.worker.running:
            messagebox.showwarning("提示", "已有分析任务正在运行")
            return

        input_mode = self.input_mode_var.get()

        input_file_text = self.osu_file_var.get().strip()
        input_dir_text = self.input_dir_var.get().strip()
        info_file_text = self.info_file_var.get().strip()
        output_dir_text = self.output_dir_var.get().strip()

        if input_mode == "file":
            if not input_file_text:
                messagebox.showerror("错误", "请选择 .osu 或 .osz 文件")
                return

        elif input_mode == "folder":
            if not input_dir_text:
                messagebox.showerror("错误", "请选择包含 .osu / .osz 的文件夹")
                return

        elif input_mode == "info":
            if not info_file_text:
                messagebox.showerror("错误", "请选择下载器 beatmap_info.json")
                return

        else:
            messagebox.showerror("错误", f"未知输入模式: {input_mode}")
            return

        if not output_dir_text:
            messagebox.showerror("错误", "请选择 JSON 保存目录")
            return

        input_file = Path(input_file_text) if input_file_text else None
        input_dir = Path(input_dir_text) if input_dir_text else None
        input_info_file = Path(info_file_text) if info_file_text else None
        output_dir = Path(output_dir_text)

        if input_mode == "file":
            if input_file is None or not input_file.exists():
                messagebox.showerror("错误", f"输入文件不存在:\n{input_file}")
                return

            if input_file.suffix.lower() not in {".osu", ".osz"}:
                messagebox.showerror("错误", "请选择 .osu 或 .osz 文件")
                return

        elif input_mode == "folder":
            if input_dir is None or not input_dir.exists() or not input_dir.is_dir():
                messagebox.showerror("错误", f"输入文件夹不存在:\n{input_dir}")
                return

        elif input_mode == "info":
            if (
                input_info_file is None
                or not input_info_file.exists()
                or not input_info_file.is_file()
            ):
                messagebox.showerror(
                    "错误",
                    f"beatmap_info.json 不存在:\n{input_info_file}",
                )
                return

            if input_info_file.suffix.lower() != ".json":
                messagebox.showerror("错误", "请选择 .json 文件")
                return

        enable_bms_analysis = bool(self.enable_bms_analysis_var.get())
        output_bms = bool(self.output_bms_var.get())

        bms_output_dir_text = self.bms_output_dir_var.get().strip()

        if output_bms and not bms_output_dir_text:
            messagebox.showerror("错误", "请选择 BMS 输出目录")
            return

        bms_output_dir = Path(bms_output_dir_text) if bms_output_dir_text else None

        is_batch_mode = input_mode in {"folder", "info"}

        # 批处理时静默过程日志；单文件时保留详细日志
        quiet_analysis_logs = is_batch_mode

        # 批处理时不保存单个 JSON；单文件时保存单个 JSON
        save_individual_json = not is_batch_mode

        # 批处理时如果不保存单个 JSON，则必须合并，否则没有输出
        merge_json_results = bool(self.merge_json_results_var.get())
        if is_batch_mode:
            merge_json_results = True

        self.worker = MixedAnalysisWorker(
            project_root=PROJECT_ROOT_DEFAULT,
            runner_file=RUNNER_FILE_DEFAULT,
            final_result_mapping_config_path=FINAL_RESULT_MAPPING_CONFIG_DEFAULT,

            input_file=input_file,
            input_dir=input_dir,
            input_info_file=input_info_file,
            input_mode=input_mode,

            batch_mode=is_batch_mode,
            merge_json_results=merge_json_results,

            save_individual_json=save_individual_json,
            quiet_analysis_logs=quiet_analysis_logs,

            output_dir=output_dir,
            enable_bms_analysis=enable_bms_analysis,
            output_bms=output_bms,
            bms_output_dir=bms_output_dir,
            log_callback=self.after_log,
            finish_callback=self.on_worker_finished,
        )

        self.set_running_ui(True)
        started = self.worker.start()

        if not started:
            self.set_running_ui(False)
            messagebox.showwarning("提示", "已有分析任务正在运行")

    def stop_run(self):
        if self.worker:
            self.worker.stop()

    def on_worker_finished(self):
        self.parent.after(0, self.set_running_ui, False)

    # ------------------------------------------------------------------
    # log / state
    # ------------------------------------------------------------------

    def clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def after_log(self, text=""):
        self.parent.after(0, self.log, text)

    def log(self, text=""):
        self.log_text.insert(tk.END, str(text) + "\n")
        self.log_text.see(tk.END)

    def set_running_ui(self, running: bool):
        try:
            if running:
                self.start_button.configure(state="disabled")
                self.stop_button.configure(state="normal")
            else:
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="normal")
        except Exception:
            pass


def build(app, parent):
    return MixedAnalysisTab(app, parent)
