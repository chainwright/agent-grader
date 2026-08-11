import json

import pytest

from app.grading import RubricError, grade, validate_rubric


def g(rubric, output, latency=None):
    payload = None
    try:
        payload = json.loads(output)
    except ValueError:
        pass
    return grade(rubric, output, payload, latency)


def test_contains_is_case_insensitive_by_default():
    v = g([{"kind": "contains", "value": "TIMEOUT"}], "request timed out: timeout after 30s")
    assert v.passed


def test_contains_respects_case_sensitive_flag():
    rubric = [{"kind": "contains", "value": "Timeout", "case_sensitive": True}]
    assert not g(rubric, "timeout").passed
    assert g(rubric, "Timeout").passed


def test_not_contains_catches_forbidden_string():
    v = g([{"kind": "not_contains", "value": "api_key"}], "here is my API_KEY=secret")
    assert not v.passed
    assert "запрещённая строка" in v.checks[0].detail


def test_json_equals_reads_nested_path():
    output = json.dumps({"result": {"items": [{"status": "done"}]}})
    assert g([{"kind": "json_equals", "path": "result.items.0.status", "value": "done"}], output).passed


def test_json_equals_reports_actual_value_on_mismatch():
    v = g([{"kind": "json_equals", "path": "status", "value": "done"}], json.dumps({"status": "failed"}))
    assert not v.passed
    assert "'failed'" in v.checks[0].detail


def test_json_check_on_plain_text_fails_without_crashing():
    v = g([{"kind": "json_equals", "path": "status", "value": "done"}], "not json at all")
    assert not v.passed
    assert "не является JSON" in v.checks[0].detail


def test_numeric_range_rejects_bool_disguised_as_number():
    # True == 1 в Python, и без явной проверки bool прошёл бы диапазон 0..2
    v = g([{"kind": "numeric_range", "path": "count", "min": 0, "max": 2}], json.dumps({"count": True}))
    assert not v.passed
    assert "ожидали число" in v.checks[0].detail


def test_numeric_range_boundaries_are_inclusive():
    rubric = [{"kind": "numeric_range", "path": "n", "min": 1, "max": 5}]
    assert g(rubric, json.dumps({"n": 1})).passed
    assert g(rubric, json.dumps({"n": 5})).passed
    assert not g(rubric, json.dumps({"n": 5.001})).passed


def test_negative_index_path_is_supported():
    output = json.dumps({"steps": ["a", "b", "final"]})
    assert g([{"kind": "json_equals", "path": "steps.-1", "value": "final"}], output).passed


def test_latency_check_fails_when_latency_missing():
    v = g([{"kind": "max_latency_ms", "value": 100}], "ok", latency=None)
    assert not v.passed
    assert "не передан" in v.checks[0].detail


def test_latency_check_compares_against_limit():
    rubric = [{"kind": "max_latency_ms", "value": 100}]
    assert g(rubric, "ok", latency=100).passed
    assert not g(rubric, "ok", latency=100.5).passed


def test_verdict_requires_all_checks_but_score_is_partial():
    rubric = [
        {"kind": "contains", "value": "alpha"},
        {"kind": "contains", "value": "beta"},
        {"kind": "contains", "value": "gamma"},
    ]
    v = g(rubric, "alpha and beta only")
    assert not v.passed
    assert v.score == pytest.approx(2 / 3, rel=1e-3)
    assert v.failed_kinds == ["contains"]


def test_score_respects_weights():
    rubric = [
        {"kind": "contains", "value": "critical", "weight": 9},
        {"kind": "contains", "value": "nice-to-have", "weight": 1},
    ]
    assert g(rubric, "critical only").score == pytest.approx(0.9)


@pytest.mark.parametrize(
    "rubric, fragment",
    [
        ([], "непустым списком"),
        ([{"kind": "nope"}], "неизвестный kind"),
        ([{"kind": "contains"}], "'value' обязателен"),
        ([{"kind": "regex", "pattern": "["}], "не компилируется"),
        ([{"kind": "numeric_range", "path": "n"}], "хотя бы один"),
        ([{"kind": "numeric_range", "path": "n", "min": 10, "max": 1}], "больше"),
        ([{"kind": "max_latency_ms", "value": 0}], "положительным"),
        ([{"kind": "contains", "value": "x", "weight": 0}], "'weight'"),
    ],
)
def test_broken_rubric_is_rejected_at_validation(rubric, fragment):
    with pytest.raises(RubricError) as exc:
        validate_rubric(rubric)
    assert fragment in str(exc.value)


def test_valid_rubric_passes_validation():
    validate_rubric(
        [
            {"kind": "contains", "value": "ok"},
            {"kind": "regex", "pattern": r"\d+"},
            {"kind": "json_equals", "path": "a.b", "value": 1},
            {"kind": "numeric_range", "path": "n", "min": 0},
            {"kind": "max_latency_ms", "value": 250},
        ]
    )
