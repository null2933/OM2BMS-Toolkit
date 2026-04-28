from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


_MISSING = object()


def parse_path(path: str) -> list[str]:
    """
    将点路径拆分成 key 列表。
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"Invalid path: {path!r}")

    return [part for part in path.split(".") if part]


def _is_int_key(key: str) -> bool:
    return key.isdigit() or (key.startswith("-") and key[1:].isdigit())


def get_by_path(data: Any, path: str, default: Any = _MISSING) -> Any:
    """
    从 dict/list 中按点路径取值。

    支持：
        compact.star
        bms.charts.0.osu_url

    如果路径不存在：
        - 提供 default 时返回 default
        - 未提供 default 时抛 KeyError / IndexError / TypeError
    """
    current = data

    for key in parse_path(path):
        if isinstance(current, Mapping):
            if key not in current:
                if default is not _MISSING:
                    return default
                raise KeyError(f"Path not found: {path!r}, missing key: {key!r}")
            current = current[key]

        elif isinstance(current, list):
            if not _is_int_key(key):
                if default is not _MISSING:
                    return default
                raise TypeError(
                    f"Path expects list index at {key!r}: {path!r}"
                )

            index = int(key)

            try:
                current = current[index]
            except IndexError:
                if default is not _MISSING:
                    return default
                raise IndexError(
                    f"List index out of range at {key!r}: {path!r}"
                )

        else:
            if default is not _MISSING:
                return default
            raise TypeError(
                f"Cannot access {key!r} on non-container object "
                f"{type(current).__name__}: {path!r}"
            )

    return current


def set_by_path(data: dict[str, Any], path: str, value: Any) -> None:
    """
    向 dict 中按点路径写入值。
    注意：
        目标路径目前主要面向 dict。
        如果目标路径中包含纯数字，例如 "charts.0.sr"，会自动创建 list。
    """
    parts = parse_path(path)
    current: Any = data

    for i, key in enumerate(parts):
        is_last = i == len(parts) - 1

        if is_last:
            if isinstance(current, list):
                if not _is_int_key(key):
                    raise TypeError(f"Expected list index, got {key!r}")

                index = int(key)
                _ensure_list_size(current, index)
                current[index] = value
            else:
                current[key] = value
            return

        next_key = parts[i + 1]
        should_create_list = _is_int_key(next_key)

        if isinstance(current, list):
            if not _is_int_key(key):
                raise TypeError(f"Expected list index, got {key!r}")

            index = int(key)
            _ensure_list_size(current, index)

            if current[index] is None:
                current[index] = [] if should_create_list else {}

            current = current[index]

        else:
            if key not in current or current[key] is None:
                current[key] = [] if should_create_list else {}

            current = current[key]


def _ensure_list_size(items: list[Any], index: int) -> None:
    if index < 0:
        raise IndexError("Negative index is not supported for setting path")

    while len(items) <= index:
        items.append(None)


def map_fields(
    source: dict[str, Any],
    mapping: Mapping[str, str],
    *,
    include_missing: bool = True,
    missing_value: Any = None,
    deep_copy_values: bool = True,
) -> dict[str, Any]:
    """
    根据字段映射生成新的 dict。

    Args:
        source:
            原始 JSON dict。

        mapping:
            字段映射配置。格式：
                {
                    "compact.star": "sr",
                    "compact.estDiff": "estimate"
                }

            即：
                source_path -> target_path

        include_missing:
            源路径不存在时是否仍然输出目标字段。

        missing_value:
            include_missing=True 时使用的默认值。

        deep_copy_values:
            是否深拷贝值。默认为 True，避免后续修改结果影响原始数据。

    Returns:
        映射后的新 dict。
    """
    result: dict[str, Any] = {}

    for source_path, target_path in mapping.items():
        value = get_by_path(source, source_path, default=_MISSING)

        if value is _MISSING:
            if include_missing:
                set_by_path(result, target_path, missing_value)
            continue

        if deep_copy_values:
            value = deepcopy(value)

        set_by_path(result, target_path, value)

    return result


def apply_field_mapping(
    source: dict[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """
    从 config 中读取字段映射并应用。

    支持配置格式：

        {
            "fields": {
                "compact.star": "sr",
                "compact.estDiff": "estimate"
            },
            "include_missing": false,
            "missing_value": null
        }

    """
    mapping = config.get("fields") or {}
    include_missing = bool(config.get("include_missing", False))
    missing_value = config.get("missing_value", None)

    return map_fields(
        source,
        mapping,
        include_missing=include_missing,
        missing_value=missing_value,
    )
