"""Движок оценки: применяет rubric к ответу агента и объясняет вердикт.

Здесь сознательно НЕ исполняется присланный код. Проверки декларативные —
их можно применить к чужому выводу, ничего не запуская. Это ограничивает
выразительность, но убирает целый класс рисков: харнесс оценки, который
запускает присланное, сам становится способом выполнить произвольный код.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class RubricError(ValueError):
    """Rubric описан неверно — ошибка автора задачи, не отвечающего."""


@dataclass(frozen=True)
class CheckResult:
    kind: str
    passed: bool
    detail: str
    weight: float


@dataclass(frozen=True)
class Verdict:
    passed: bool
    score: float
    checks: list[CheckResult]

    @property
    def failed_kinds(self) -> list[str]:
        return [c.kind for c in self.checks if not c.passed]


def _dig(payload: Any, path: str) -> tuple[bool, Any]:
    """Достать значение по пути вида `a.b.0.c`. Возвращает (нашли?, значение)."""
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list):
            if not part.lstrip("-").isdigit():
                return False, None
            index = int(part)
            if not -len(current) <= index < len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _check_contains(spec: dict, output: str, _payload: Any) -> tuple[bool, str]:
    needle = spec.get("value")
    if not isinstance(needle, str):
        raise RubricError("contains: 'value' обязателен и должен быть строкой")
    haystack = output if spec.get("case_sensitive", False) else output.lower()
    probe = needle if spec.get("case_sensitive", False) else needle.lower()
    if probe in haystack:
        return True, f"нашли {needle!r}"
    return False, f"в ответе нет {needle!r}"


def _check_not_contains(spec: dict, output: str, payload: Any) -> tuple[bool, str]:
    found, detail = _check_contains(spec, output, payload)
    if found:
        return False, f"запрещённая строка присутствует: {spec['value']!r}"
    return True, detail.replace("в ответе нет", "верно отсутствует")


def _check_regex(spec: dict, output: str, _payload: Any) -> tuple[bool, str]:
    pattern = spec.get("pattern")
    if not isinstance(pattern, str):
        raise RubricError("regex: 'pattern' обязателен и должен быть строкой")
    try:
        compiled = re.compile(pattern, 0 if spec.get("case_sensitive", False) else re.I)
    except re.error as exc:
        raise RubricError(f"regex: шаблон не компилируется — {exc}") from exc
    if compiled.search(output):
        return True, f"шаблон {pattern!r} сработал"
    return False, f"шаблон {pattern!r} не найден"


def _check_json_equals(spec: dict, _output: str, payload: Any) -> tuple[bool, str]:
    path = spec.get("path")
    if not isinstance(path, str) or not path:
        raise RubricError("json_equals: 'path' обязателен")
    if payload is None:
        return False, "ответ не является JSON — сравнивать нечего"
    found, actual = _dig(payload, path)
    if not found:
        return False, f"пути {path!r} нет в ответе"
    expected = spec.get("value")
    if actual == expected:
        return True, f"{path} == {expected!r}"
    return False, f"{path}: ожидали {expected!r}, получили {actual!r}"


def _check_numeric_range(spec: dict, _output: str, payload: Any) -> tuple[bool, str]:
    path = spec.get("path")
    if not isinstance(path, str) or not path:
        raise RubricError("numeric_range: 'path' обязателен")
    low, high = spec.get("min"), spec.get("max")
    if low is None and high is None:
        raise RubricError("numeric_range: нужен хотя бы один из 'min'/'max'")
    if low is not None and high is not None and low > high:
        raise RubricError("numeric_range: 'min' больше 'max'")
    if payload is None:
        return False, "ответ не является JSON — числа не достать"
    found, actual = _dig(payload, path)
    if not found:
        return False, f"пути {path!r} нет в ответе"
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False, f"{path}: ожидали число, получили {type(actual).__name__}"
    if low is not None and actual < low:
        return False, f"{path}={actual} меньше минимума {low}"
    if high is not None and actual > high:
        return False, f"{path}={actual} больше максимума {high}"
    return True, f"{path}={actual} в диапазоне"


def _check_max_latency(spec: dict, _output: str, _payload: Any) -> tuple[bool, str]:
    raise RubricError("max_latency_ms проверяется отдельно, вне общего пути")


CHECKS = {
    "contains": _check_contains,
    "not_contains": _check_not_contains,
    "regex": _check_regex,
    "json_equals": _check_json_equals,
    "numeric_range": _check_numeric_range,
}


def validate_rubric(rubric: list[dict]) -> None:
    """Проверить rubric на этапе создания задачи, а не при первой попытке.

    Иначе кривой критерий всплывает как «агент не прошёл», хотя виноват автор.
    """
    if not isinstance(rubric, list) or not rubric:
        raise RubricError("rubric должен быть непустым списком проверок")
    for i, spec in enumerate(rubric):
        if not isinstance(spec, dict):
            raise RubricError(f"проверка #{i}: ожидался объект")
        kind = spec.get("kind")
        if kind == "max_latency_ms":
            limit = spec.get("value")
            if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit <= 0:
                raise RubricError("max_latency_ms: 'value' должен быть положительным числом")
            continue
        if kind not in CHECKS:
            known = ", ".join(sorted(set(CHECKS) | {"max_latency_ms"}))
            raise RubricError(f"проверка #{i}: неизвестный kind {kind!r}. Доступны: {known}")
        weight = spec.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise RubricError(f"проверка #{i}: 'weight' должен быть положительным числом")
        # прогон на пустом ответе ловит структурные ошибки спецификации
        CHECKS[kind](spec, "", None)


def grade(rubric: list[dict], output: str, payload: Any, latency_ms: float | None) -> Verdict:
    """Применить rubric. Вердикт — passed только если прошли ВСЕ проверки.

    score — доля веса пройденных проверок: она нужна, чтобы отличать
    «ответ мимо целиком» от «ответ почти верный», не размывая сам вердикт.
    """
    results: list[CheckResult] = []
    for spec in rubric:
        kind = spec["kind"]
        weight = float(spec.get("weight", 1.0))
        if kind == "max_latency_ms":
            limit = float(spec["value"])
            if latency_ms is None:
                results.append(CheckResult(kind, False, "latency_ms не передан", weight))
            elif latency_ms <= limit:
                results.append(CheckResult(kind, True, f"{latency_ms:.0f}ms <= {limit:.0f}ms", weight))
            else:
                results.append(CheckResult(kind, False, f"{latency_ms:.0f}ms > {limit:.0f}ms", weight))
            continue
        passed, detail = CHECKS[kind](spec, output, payload)
        results.append(CheckResult(kind, passed, detail, weight))

    total = sum(r.weight for r in results)
    earned = sum(r.weight for r in results if r.passed)
    return Verdict(
        passed=all(r.passed for r in results),
        score=round(earned / total, 4) if total else 0.0,
        checks=results,
    )
