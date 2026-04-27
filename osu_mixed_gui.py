import json
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

import zipfile
import tempfile
import shutil


PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parent


class OsuMixedGui(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("osu!mania Mixed Difficulty + Pattern GUI")
        self.geometry("1120x850")

        self.project_root_var = tk.StringVar(value=str(PROJECT_ROOT_DEFAULT))
        self.runner_file_var = tk.StringVar(
            value=str(PROJECT_ROOT_DEFAULT / "gui_mixed_runner.mjs")
        )
        self.osu_file_var = tk.StringVar(value="")

        # JSON 保存目录
        self.output_dir_var = tk.StringVar(
            value=str(PROJECT_ROOT_DEFAULT / "json_results")
        )

        self.speed_rate_var = tk.StringVar(value="1.0")
        self.cvt_flag_var = tk.StringVar(value="")
        self.with_graph_var = tk.BooleanVar(value=False)

        self.process = None
        self.temp_dir = None

        self.build_ui()

    def build_ui(self):
        pad = 6

        root = tk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        tk.Label(root, text="项目根目录:").grid(
            row=row, column=0, sticky="w", padx=pad, pady=pad
        )
        tk.Entry(root, textvariable=self.project_root_var, width=100).grid(
            row=row, column=1, sticky="we", padx=pad, pady=pad
        )
        tk.Button(root, text="选择", command=self.choose_project_root).grid(
            row=row, column=2, padx=pad, pady=pad
        )

        row += 1

        tk.Label(root, text="Node runner .mjs:").grid(
            row=row, column=0, sticky="w", padx=pad, pady=pad
        )
        tk.Entry(root, textvariable=self.runner_file_var, width=100).grid(
            row=row, column=1, sticky="we", padx=pad, pady=pad
        )
        tk.Button(root, text="选择", command=self.choose_runner_file).grid(
            row=row, column=2, padx=pad, pady=pad
        )

        row += 1

        tk.Label(root, text=".osu/.osz 文件:").grid(
            row=row, column=0, sticky="w", padx=pad, pady=pad
        )
        tk.Entry(root, textvariable=self.osu_file_var, width=100).grid(
            row=row, column=1, sticky="we", padx=pad, pady=pad
        )
        tk.Button(root, text="选择", command=self.choose_osu_file).grid(
            row=row, column=2, padx=pad, pady=pad
        )

        row += 1

        tk.Label(root, text="JSON 保存目录:").grid(
            row=row, column=0, sticky="w", padx=pad, pady=pad
        )
        tk.Entry(root, textvariable=self.output_dir_var, width=100).grid(
            row=row, column=1, sticky="we", padx=pad, pady=pad
        )
        tk.Button(root, text="选择", command=self.choose_output_dir).grid(
            row=row, column=2, padx=pad, pady=pad
        )

        row += 1

        options_frame = tk.Frame(root)
        options_frame.grid(
            row=row, column=0, columnspan=3, sticky="we", padx=pad, pady=pad
        )

        tk.Label(options_frame, text="speedRate:").pack(side=tk.LEFT)
        tk.Entry(options_frame, textvariable=self.speed_rate_var, width=10).pack(
            side=tk.LEFT, padx=(4, 16)
        )

        tk.Label(options_frame, text="cvtFlag:").pack(side=tk.LEFT)
        tk.Entry(options_frame, textvariable=self.cvt_flag_var, width=18).pack(
            side=tk.LEFT, padx=(4, 16)
        )

        tk.Checkbutton(
            options_frame,
            text="withGraph",
            variable=self.with_graph_var
        ).pack(side=tk.LEFT, padx=(4, 16))

        tk.Button(
            options_frame,
            text="开始分析",
            command=self.start_run,
            width=14
        ).pack(side=tk.LEFT, padx=8)

        tk.Button(
            options_frame,
            text="停止",
            command=self.stop_run,
            width=10
        ).pack(side=tk.LEFT, padx=8)

        tk.Button(
            options_frame,
            text="清空日志",
            command=self.clear_log,
            width=10
        ).pack(side=tk.LEFT, padx=8)

        row += 1

        self.log_text = scrolledtext.ScrolledText(
            root,
            wrap=tk.WORD,
            font=("Consolas", 10)
        )
        self.log_text.grid(
            row=row,
            column=0,
            columnspan=3,
            sticky="nsew",
            padx=pad,
            pady=pad
        )

        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(row, weight=1)

    def choose_project_root(self):
        path = filedialog.askdirectory(
            title="选择项目根目录",
            initialdir=self.project_root_var.get() or str(PROJECT_ROOT_DEFAULT),
        )
        if path:
            self.project_root_var.set(path)
            runner = Path(path) / "gui_mixed_runner.mjs"
            if runner.exists():
                self.runner_file_var.set(str(runner))

    def choose_runner_file(self):
        path = filedialog.askopenfilename(
            title="选择 Node runner .mjs 文件",
            filetypes=[
                ("Node ESM Runner", "*.mjs"),
                ("JavaScript", "*.js"),
                ("All files", "*.*"),
            ],
            initialdir=self.project_root_var.get() or str(PROJECT_ROOT_DEFAULT),
        )
        if path:
            self.runner_file_var.set(path)

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

    def choose_output_dir(self):
        path = filedialog.askdirectory(
            title="选择 JSON 保存目录",
            initialdir=self.output_dir_var.get() or str(PROJECT_ROOT_DEFAULT),
        )
        if path:
            self.output_dir_var.set(path)

    def extract_osz(self, osz_path):
        """
        解压 .osz 到临时目录，返回所有 .osu 文件路径
        """
        if self.temp_dir:
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass

        self.temp_dir = tempfile.mkdtemp(prefix="osz_")

        with zipfile.ZipFile(osz_path, "r") as z:
            z.extractall(self.temp_dir)

        osu_files = list(Path(self.temp_dir).glob("*.osu"))
        return sorted(osu_files)

    def validate_inputs(self):
        project_root = Path(self.project_root_var.get().strip())
        runner_file = Path(self.runner_file_var.get().strip())
        input_file = Path(self.osu_file_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip())

        if not project_root.exists() or not project_root.is_dir():
            messagebox.showerror("错误", "项目根目录不存在")
            return None

        if not runner_file.exists() or not runner_file.is_file():
            messagebox.showerror("错误", "Node runner 文件不存在")
            return None

        if not input_file.exists() or not input_file.is_file():
            messagebox.showerror("错误", "输入文件不存在")
            return None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", f"无法创建 JSON 保存目录: {e}")
            return None

        if not output_dir.exists() or not output_dir.is_dir():
            messagebox.showerror("错误", "JSON 保存路径必须是目录")
            return None

        try:
            speed_rate = float(self.speed_rate_var.get().strip() or "1.0")
        except ValueError:
            messagebox.showerror("错误", "speedRate 必须是数字")
            return None

        cvt_flag = self.cvt_flag_var.get().strip()
        with_graph = self.with_graph_var.get()

        # 判断是 .osz 还是 .osu
        if input_file.suffix.lower() == ".osz":
            try:
                osu_files = self.extract_osz(input_file)
                if not osu_files:
                    messagebox.showerror("错误", ".osz 中没有找到 .osu 文件")
                    return None
            except Exception as e:
                messagebox.showerror("错误", f"解压 .osz 失败: {e}")
                return None
        else:
            osu_files = [input_file]

        return {
            "project_root": project_root,
            "runner_file": runner_file,
            "osu_files": osu_files,
            "output_dir": output_dir,
            "speed_rate": speed_rate,
            "cvt_flag": cvt_flag,
            "with_graph": with_graph,
        }

    def start_run(self):
        if self.process is not None:
            messagebox.showwarning("提示", "已有分析进程正在运行")
            return

        args = self.validate_inputs()
        if not args:
            return

        t = threading.Thread(
            target=self.run_all_osu_files,
            args=(args,),
            daemon=True
        )
        t.start()

    def run_all_osu_files(self, args):
        osu_files = args["osu_files"]
        output_dir = args["output_dir"]

        self.after(0, self.log, f"找到 {len(osu_files)} 个 .osu 文件")
        self.after(0, self.log, f"JSON 保存目录: {output_dir}")
        self.after(0, self.log, "=" * 60)

        for idx, osu_file in enumerate(osu_files, 1):
            self.after(0, self.log, f"\n[{idx}/{len(osu_files)}] {osu_file.name}")
            self.after(0, self.log, "-" * 60)

            single_args = args.copy()
            single_args["osu_file"] = osu_file

            self.run_node_process(single_args)

        self.after(0, self.log, "\n" + "=" * 60)
        self.after(0, self.log, f"全部完成，共分析 {len(osu_files)} 个谱面")
        self.after(0, self.log, "=" * 60)

    def run_node_process(self, args):
        project_root = args["project_root"]
        runner_file = args["runner_file"]
        osu_file = args["osu_file"]
        output_dir = args["output_dir"]
        speed_rate = args["speed_rate"]
        cvt_flag = args["cvt_flag"]
        with_graph = args["with_graph"]

        cmd = [
            "node",
            str(runner_file),
            str(project_root),
            str(osu_file),
            str(speed_rate),
            cvt_flag,
            "true" if with_graph else "false",
        ]

        stdout_chunks = []

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(project_root),
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

            self.after(
                0,
                self.save_json_and_print_summary,
                full_stdout,
                osu_file,
                output_dir,
                exit_code,
            )

        except FileNotFoundError:
            self.after(0, self.log, "[GUI ERROR] 找不到 node 命令")
        except Exception as exc:
            self.after(0, self.log, f"[GUI ERROR] {exc}")
        finally:
            self.process = None

    def read_stream(self, stream, prefix, collect):
        try:
            for line in stream:
                if collect is not None:
                    collect.append(line)
        except Exception:
            pass


    def extract_json_from_stdout(self, stdout_text):
        """
        从 Node stdout 中提取 JSON
        """
        text = stdout_text.strip()
        if not text:
            return None

        # 1. 直接解析整个 stdout
        try:
            return json.loads(text)
        except Exception:
            pass

        # 2. 从最后一行开始找 JSON
        lines = text.splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except Exception:
                    pass

        # 3. 兜底：截取第一个 { 到最后一个 }
        first = text.find("{")
        last = text.rfind("}")

        if first >= 0 and last > first:
            candidate = text[first:last + 1]
            try:
                return json.loads(candidate)
            except Exception:
                return None

        return None

    def make_unique_json_path(self, output_dir: Path, osu_file: Path):
        """
        根据 osu 文件名生成不重复的 json 保存路径。

        example.osu -> example.json
        example_1.json
        example_2.json
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = osu_file.stem
        json_path = output_dir / f"{base_name}.json"

        if not json_path.exists():
            return json_path

        idx = 1
        while True:
            candidate = output_dir / f"{base_name}_{idx}.json"
            if not candidate.exists():
                return candidate
            idx += 1

    def save_json_and_print_summary(self, stdout_text, osu_file: Path, output_dir: Path, exit_code):
        """
        关键逻辑：

        1. 解析 Node 返回 JSON
        2. summaryText 不保存到 JSON
        3. summaryText 直接打印到 GUI
        4. 其他字段保存到指定 JSON 文件
        """
        data = self.extract_json_from_stdout(stdout_text)

        if not data:
            self.log("[GUI] 无法解析 JSON，未保存")
            return

        # 取出 summaryText，用于直接打印
        summary_text = data.get("summaryText")

        # 复制一份用于保存，避免修改原 data
        json_data = dict(data)

        # 不把 summaryText 写入 json
        json_data.pop("summaryText", None)

        # 附加 GUI 侧信息，可选
        json_data["_gui"] = {
            "sourceOsu": str(osu_file),
            "exitCode": exit_code,
        }

        json_path = self.make_unique_json_path(output_dir, osu_file)

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)

            if data.get("ok"):
                self.log(f"[GUI] JSON 已保存: {json_path}")
            else:
                self.log(f"[GUI] 分析失败 JSON 已保存: {json_path}")
                self.log(f"[GUI] 错误: {data.get('error')}")

        except Exception as e:
            self.log(f"[GUI] 保存 JSON 失败: {e}")
            return

        # summaryText 直接打印，但不进入 JSON
        if summary_text:
            self.log("")
            self.log(summary_text)

    def clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def log(self, text=""):
        self.log_text.insert(tk.END, str(text) + "\n")
        self.log_text.see(tk.END)
        self.update_idletasks()

    def stop_run(self):
        if self.process is not None:
            try:
                self.process.kill()
                self.log("[GUI] 已停止")
            except Exception as exc:
                self.log(f"[GUI] 停止失败: {exc}")


if __name__ == "__main__":
    app = OsuMixedGui()
    app.mainloop()
