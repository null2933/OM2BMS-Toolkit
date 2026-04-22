from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DifficultyMapping:
    table: str
    level: float
    display: str
    label: str
    sub_label: str


class BMSDifficultyMapper:
    def __init__(self) -> None:
        self.total_div = 27.0
        self.sl_offset = 1.0
        self.st_offset = 14.0

    def _get_sub_label(self, raw_val: float, prefix: str) -> str:
        base_x = int(raw_val + 0.5)
        remainder = raw_val - base_x

        if remainder < -0.2:
            sub_mod = "-"
        elif remainder < 0.2:
            sub_mod = ""
        else:
            sub_mod = "+"
        return f"{prefix}{base_x}{sub_mod}"

    def denormalize(self, value: float) -> DifficultyMapping:
        y = max(0.0, min(1.0, float(value)))

        threshold_sl_minus = 0.5 / self.total_div
        threshold_st_start = 13.5 / self.total_div
        threshold_st_plus = 26.5 / self.total_div

        if y < threshold_sl_minus:
            return DifficultyMapping("sl-", 0.0, "sl-", "sl-", "sl-")

        if y < threshold_st_start:
            raw_val = y * self.total_div - self.sl_offset
            level = 0.0 if round(raw_val, 1) == 0.0 else raw_val
            return DifficultyMapping(
                table="sl",
                level=level,
                display=f"sl{level:.1f}",
                label=f"sl{round(level)}",
                sub_label=self._get_sub_label(raw_val, "sl"),
            )

        if y < threshold_st_plus:
            raw_val = y * self.total_div - self.st_offset
            level = 0.0 if round(raw_val, 1) == 0.0 else raw_val
            return DifficultyMapping(
                table="st",
                level=level,
                display=f"st{level:.1f}",
                label=f"st{round(level)}",
                sub_label=self._get_sub_label(raw_val, "st"),
            )

        return DifficultyMapping("st+", 0.0, "st+", "st+", "st+")

