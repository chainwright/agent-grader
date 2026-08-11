import asyncio
import json

import pytest

pytestmark = pytest.mark.asyncio

RUBRIC = [
    {"kind": "json_equals", "path": "status", "value": "done"},
    {"kind": "max_latency_ms", "value": 500},
]


async def make_task(client, slug="demo", rubric=None):
    r = await client.post(
        "/tasks", json={"slug": slug, "prompt": "Верни {\"status\": \"done\"}", "rubric": rubric or RUBRIC}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_create_task_and_list(client):
    await make_task(client)
    r = await client.get("/tasks")
    assert r.status_code == 200
    assert [t["slug"] for t in r.json()] == ["demo"]


async def test_duplicate_slug_is_conflict(client):
    await make_task(client)
    r = await client.post("/tasks", json={"slug": "demo", "prompt": "x", "rubric": RUBRIC})
    assert r.status_code == 409


async def test_broken_rubric_is_rejected_with_reason(client):
    r = await client.post(
        "/tasks", json={"slug": "bad", "prompt": "x", "rubric": [{"kind": "regex", "pattern": "["}]}
    )
    assert r.status_code == 422
    assert "не компилируется" in r.json()["detail"]


async def test_submission_passes_and_reports_checks(client):
    await make_task(client)
    r = await client.post(
        "/tasks/demo/submissions",
        json={"agent": "claude", "output": json.dumps({"status": "done"}), "latency_ms": 120},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["passed"] is True
    assert body["score"] == 1.0
    assert {c["kind"] for c in body["checks"]} == {"json_equals", "max_latency_ms"}


async def test_submission_fails_on_slow_but_correct_answer(client):
    await make_task(client)
    r = await client.post(
        "/tasks/demo/submissions",
        json={"agent": "slowpoke", "output": json.dumps({"status": "done"}), "latency_ms": 900},
    )
    body = r.json()
    assert body["passed"] is False
    assert body["score"] == 0.5  # содержимое верное, не уложился по времени
    failed = [c for c in body["checks"] if not c["passed"]]
    assert failed[0]["kind"] == "max_latency_ms"


async def test_submission_to_unknown_task_is_404(client):
    r = await client.post("/tasks/ghost/submissions", json={"agent": "a", "output": "x"})
    assert r.status_code == 404


async def test_idempotency_key_does_not_create_second_attempt(client):
    await make_task(client)
    payload = {
        "agent": "claude",
        "output": json.dumps({"status": "done"}),
        "latency_ms": 100,
        "idempotency_key": "retry-1",
    }
    first = await client.post("/tasks/demo/submissions", json=payload)
    second = await client.post("/tasks/demo/submissions", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["duplicate_of"] == first.json()["id"]

    stats = (await client.get("/tasks/demo/stats")).json()
    assert stats["submissions"] == 1


async def test_concurrent_retries_with_same_key_collapse_to_one(client):
    await make_task(client)
    payload = {
        "agent": "claude",
        "output": json.dumps({"status": "done"}),
        "latency_ms": 100,
        "idempotency_key": "race",
    }
    await asyncio.gather(
        *(client.post("/tasks/demo/submissions", json=payload) for _ in range(4))
    )
    stats = (await client.get("/tasks/demo/stats")).json()
    assert stats["submissions"] == 1


async def test_stats_are_empty_for_task_without_submissions(client):
    await make_task(client)
    stats = (await client.get("/tasks/demo/stats")).json()
    assert stats["submissions"] == 0
    assert stats["pass_rate"] == 0.0
    assert stats["p95_latency_ms"] is None


async def test_stats_aggregate_pass_rate_and_failures(client):
    await make_task(client)
    answers = [
        (json.dumps({"status": "done"}), 100),   # прошёл
        (json.dumps({"status": "done"}), 700),   # медленный
        (json.dumps({"status": "failed"}), 90),  # неверный
        ("not json", 80),                        # вообще не JSON
    ]
    for output, latency in answers:
        await client.post(
            "/tasks/demo/submissions",
            json={"agent": "claude", "output": output, "latency_ms": latency},
        )

    stats = (await client.get("/tasks/demo/stats")).json()
    assert stats["submissions"] == 4
    assert stats["passed"] == 1
    assert stats["pass_rate"] == 0.25
    breakdown = {f["kind"]: f["failures"] for f in stats["failures_by_check"]}
    assert breakdown == {"json_equals": 2, "max_latency_ms": 1}


async def test_p95_latency_is_an_observed_value(client):
    await make_task(client)
    for latency in (10, 20, 30, 40, 1000):
        await client.post(
            "/tasks/demo/submissions",
            json={"agent": "a", "output": json.dumps({"status": "done"}), "latency_ms": latency},
        )
    stats = (await client.get("/tasks/demo/stats")).json()
    assert stats["p95_latency_ms"] == 1000  # не интерполяция, а реальный замер
