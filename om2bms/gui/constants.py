from __future__ import annotations

# 应用标题
APP_TITLE = "OM2BMS Toolkit"



DEFAULT_HITSOUND_ENABLED = False
DEFAULT_BG_ENABLED = True
DEFAULT_OFFSET_VALUE = "0"
DEFAULT_TN_VALUE = "0.2"
DEFAULT_JUDGE_VALUE = "EASY"



# ============================================================
# 判定选项
# 注意：
# 这里的值请替换成你原代码里的真实 JUDGE_OPTIONS。
# 如果你原来已经有 JUDGE_OPTIONS，直接复制过来即可。
# ============================================================
JUDGE_OPTIONS = {
    "EASY": 3,
    "NORMAL": 2,
    "HARD": 1,
    "VERYHARD": 0,
}


# ============================================================
# 难度分析模式标签
# 注意：
# key 应该与你 DifficultyAnalysisMode 的 value 保持一致。
# ============================================================
ANALYSIS_MODE_LABELS = {
    "off": "关闭",
    "all": "全部谱面",
    "single": "指定谱面",
}


ANALYSIS_MODE_VALUE_BY_LABEL = {
    label: value for value, label in ANALYSIS_MODE_LABELS.items()
}


# ============================================================
# 转换结果 CSV 导出字段
# 左边是内部 key，右边是 CSV 表头
# ============================================================
EXPORT_COLUMNS = [
    ("archive_name", "archive_name"),
    ("output_directory", "output_directory"),
    ("chart_id", "chart_id"),
    ("chart_index", "chart_index"),
    ("source_chart_name", "source_chart_name"),
    ("source_osu_path", "source_osu_path"),
    ("source_difficulty_label", "source_difficulty_label"),
    ("output_file_name", "output_file_name"),
    ("output_path", "output_path"),
    ("conversion_status", "conversion_status"),
    ("conversion_error", "conversion_error"),
    ("analysis_enabled", "analysis_enabled"),
    ("analysis_status", "analysis_status"),
    ("estimated_difficulty", "estimated_difficulty"),
    ("raw_score", "raw_score"),
    ("difficulty_table", "difficulty_table"),
    ("analysis_label", "analysis_label"),
    ("difficulty_display", "difficulty_display"),
    ("analysis_source", "analysis_source"),
    ("runtime_provider", "runtime_provider"),
    ("analysis_error", "analysis_error"),
    ("analysis_selection_error", "analysis_selection_error"),
]


# AnalyzerTab 导出字段
ANALYZER_EXPORT_FIELDS = [
    "Chart",
    "Difficulty",
    "Level",
    "Raw",
    "Source",
]


# TableGenTab 导出字段
TABLEGEN_EXPORT_FIELDS = [
    "Chart",
    "Title",
    "Artist",
    "Level",
    "Status",
    "Appended",
]
