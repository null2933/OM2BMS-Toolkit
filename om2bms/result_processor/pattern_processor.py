from __future__ import annotations

from typing import Any


def to_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def format_percent(value: Any, digits: int = 2) -> str | None:
    """
    输入已经是百分比值时使用。

    Example:
        37.4213 -> "37.42%"
    """

    number = to_float(value)

    if number is None:
        return None

    rounded = round(number, digits)

    if rounded.is_integer():
        return f"{int(rounded)}%"

    return f"{rounded}%"


def get_by_path(data: Any, path: str, default: Any = None) -> Any:
    current: Any = data

    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return default

            current = current[part]
            continue

        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return default

            if index < 0 or index >= len(current):
                return default

            current = current[index]
            continue

        return default

    return current


def get_pattern_clusters(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    优先使用 pattern.topFiveClusters。

    如果之后你的 rawdata 里有完整 clusters，例如 pattern.clusters，
    可以在这里扩展。
    """

    clusters = get_by_path(data, "pattern.topFiveClusters")

    if isinstance(clusters, list):
        return [
            cluster
            for cluster in clusters
            if isinstance(cluster, dict)
        ]

    return []


def collect_pattern_amounts_from_summary(data: dict[str, Any]) -> dict[str, float]:
    """
    从 pattern.summary.byPattern 读取大类总 amount。

    rawdata 示例结构大概是：

    {
      "pattern": {
        "summary": {
          "byPattern": {
            "Chordstream": {
              "count": 3,
              "totalAmount": 347545
            }
          }
        }
      }
    }
    """

    by_pattern = get_by_path(data, "pattern.summary.byPattern")

    if not isinstance(by_pattern, dict):
        return {}

    result: dict[str, float] = {}

    for pattern_name, info in by_pattern.items():
        if not isinstance(info, dict):
            continue

        amount = to_float(info.get("totalAmount"))

        if amount is None:
            continue

        result[str(pattern_name)] = amount

    return result


def collect_pattern_amounts_from_clusters(
    clusters: list[dict[str, Any]],
) -> dict[str, float]:
    """
    如果 summary.byPattern 不存在，则从 clusters 里按 Pattern 合并 Amount。
    """

    result: dict[str, float] = {}

    for cluster in clusters:
        pattern_name = cluster.get("Pattern")
        amount = to_float(cluster.get("Amount"))

        if pattern_name is None or amount is None:
            continue

        pattern_name = str(pattern_name)

        result[pattern_name] = result.get(pattern_name, 0.0) + amount

    return result


def collect_specific_type_amounts_by_pattern(
    clusters: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """
    按大类 Pattern 合并所有 cluster 的 SpecificTypes。

    关键点：
    SpecificTypes 里的 ratio 通常是当前 cluster 内部的小类比例。

    Example:
        cluster.Amount = 1000
        SpecificTypes = [
            ["Dense Chordstream", 0.6],
            ["Light Chordstream", 0.4]
        ]

    那么：
        Dense Chordstream amount += 1000 * 0.6
        Light Chordstream amount += 1000 * 0.4
    """

    result: dict[str, dict[str, float]] = {}

    for cluster in clusters:
        pattern_name = cluster.get("Pattern")
        cluster_amount = to_float(cluster.get("Amount"))
        specific_types = cluster.get("SpecificTypes")

        if pattern_name is None:
            continue

        if cluster_amount is None:
            continue

        if not isinstance(specific_types, list):
            continue

        pattern_name = str(pattern_name)

        if pattern_name not in result:
            result[pattern_name] = {}

        for item in specific_types:
            if not isinstance(item, list) or len(item) < 2:
                continue

            specific_name = item[0]
            specific_ratio = to_float(item[1])

            if specific_name is None or specific_ratio is None:
                continue

            specific_name = str(specific_name)
            specific_amount = cluster_amount * specific_ratio

            result[pattern_name][specific_name] = (
                result[pattern_name].get(specific_name, 0.0) + specific_amount
            )

    return result


def build_one_pattern_text(
    pattern_name: str,
    *,
    pattern_amount: float,
    total_amount: float,
    specific_amounts: dict[str, float] | None,
    digits: int = 2,
) -> str | None:
    """
    构造单个大类的输出文本。

    Example:
        Chordstream (37.42%): Dense Chordstream (36.9%), Double Stream (34.8%)
    """

    if total_amount <= 0:
        return None

    pattern_percent = pattern_amount / total_amount * 100
    pattern_percent_text = format_percent(pattern_percent, digits)

    if pattern_percent_text is None:
        return None

    prefix = f"{pattern_name} ({pattern_percent_text})"

    if not specific_amounts:
        return f"{prefix}: -"

    specific_parts: list[str] = []

    sorted_specific_items = sorted(
        specific_amounts.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    for specific_name, specific_amount in sorted_specific_items:
        if pattern_amount <= 0:
            continue

        specific_percent = specific_amount / pattern_amount * 100
        specific_percent_text = format_percent(specific_percent, digits)

        if specific_percent_text is None:
            continue

        specific_parts.append(f"{specific_name} ({specific_percent_text})")

    if not specific_parts:
        return f"{prefix}: -"

    return f"{prefix}: {', '.join(specific_parts)}"


def build_pattern_lines(
    data: dict[str, Any],
    *,
    min_pattern_ratio: float = 0.10,
    digits: int = 2,
) -> list[str] | None:
    """
    构建最终 pattern 文本列表。

    规则：
        1. 按 Pattern 大类合并 cluster；
        2. 计算大类占所有 pattern amount 的比例；
        3. 大类比例小于 min_pattern_ratio 的不输出；
        4. 大类内部的小类比例按 SpecificTypes 合并计算；
        5. 小类百分比 = 小类 amount / 大类 amount。
    """

    clusters = get_pattern_clusters(data)

    # 优先从 summary.byPattern 取大类 amount。
    pattern_amounts = collect_pattern_amounts_from_summary(data)

    # 如果没有 summary，则 fallback 到 clusters。
    if not pattern_amounts:
        pattern_amounts = collect_pattern_amounts_from_clusters(clusters)

    if not pattern_amounts:
        return None

    total_amount = sum(pattern_amounts.values())

    if total_amount <= 0:
        return None

    specific_amounts_by_pattern = collect_specific_type_amounts_by_pattern(clusters)

    result: list[str] = []

    sorted_pattern_items = sorted(
        pattern_amounts.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    for pattern_name, pattern_amount in sorted_pattern_items:
        pattern_ratio = pattern_amount / total_amount

        # 大类不足 10%，不输出
        if pattern_ratio < min_pattern_ratio:
            continue

        text = build_one_pattern_text(
            pattern_name,
            pattern_amount=pattern_amount,
            total_amount=total_amount,
            specific_amounts=specific_amounts_by_pattern.get(pattern_name),
            digits=digits,
        )

        if text:
            result.append(text)

    if not result:
        return None

    return result


def build_pattern_fields(data: dict[str, Any]) -> dict[str, Any]:
    """
    给 final_result_processor 调用。

    最终只输出一个字段：
        patterns: list[str] | None
    """

    return {
        "patterns": build_pattern_lines(
            data,
            min_pattern_ratio=0.10,
            digits=2,
        ),
    }
