from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.db import create_all, get_session
from app.grading import RubricError, grade, validate_rubric


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_all()
    yield


app = FastAPI(
    title="agent-grader",
    version="0.1.0",
    summary="Оценка ответов AI-агентов по декларативным критериям, без запуска присланного кода.",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tasks", response_model=schemas.TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(payload: schemas.TaskIn, session: AsyncSession = Depends(get_session)):
    try:
        validate_rubric(payload.rubric)
    except RubricError as exc:
        # 422, а не 500: кривой rubric — это ошибка автора задачи, и он должен
        # узнать о ней при создании, а не увидеть её как «агент не прошёл».
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    task = models.Task(slug=payload.slug, prompt=payload.prompt, rubric=payload.rubric)
    session.add(task)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"slug {payload.slug!r} уже занят") from exc
    await session.refresh(task)
    return task


@app.get("/tasks", response_model=list[schemas.TaskOut])
async def list_tasks(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(models.Task).order_by(models.Task.id))
    return list(rows)


async def _get_task(slug: str, session: AsyncSession) -> models.Task:
    task = await session.scalar(select(models.Task).where(models.Task.slug == slug))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"задачи {slug!r} нет")
    return task


def _parse_json(output: str) -> Any:
    try:
        return json.loads(output)
    except (ValueError, TypeError):
        return None


@app.post(
    "/tasks/{slug}/submissions",
    response_model=schemas.SubmissionOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit(
    slug: str,
    payload: schemas.SubmissionIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    task = await _get_task(slug, session)
    # id снимаем заранее: после отката гонки объект task может оказаться
    # недоступен для ленивой подгрузки, а id к тому моменту уже нужен.
    task_id = task.id

    if payload.idempotency_key:
        existing = await session.scalar(
            select(models.Submission).where(
                models.Submission.task_id == task.id,
                models.Submission.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            # Повтор по тому же ключу возвращает исходный результат и 200:
            # ретрай по таймауту не должен ни удваивать попытки, ни менять вердикт.
            response.status_code = status.HTTP_200_OK
            out = schemas.SubmissionOut.model_validate(existing)
            return out.model_copy(update={"duplicate_of": existing.id})

    verdict = grade(task.rubric, payload.output, _parse_json(payload.output), payload.latency_ms)
    submission = models.Submission(
        task_id=task.id,
        agent=payload.agent,
        output=payload.output,
        latency_ms=payload.latency_ms,
        idempotency_key=payload.idempotency_key,
        passed=verdict.passed,
        score=verdict.score,
        checks=[c.__dict__ for c in verdict.checks],
    )
    session.add(submission)
    try:
        await session.commit()
    except IntegrityError:
        # Гонка: два одновременных ретрая с одним ключом. Победил другой —
        # отдаём его результат, а не создаём вторую попытку.
        # Ниже используется task_id, снятый ДО вставки, а не task.id: откат
        # переводит task в expired-состояние, и обращение к его атрибуту
        # запускает ленивую подгрузку вне greenlet-контекста — вместо честного
        # ответа прилетает MissingGreenlet. Поймано тестом на четыре
        # одновременных ретрая с одним ключом.
        await session.rollback()
        existing = await session.scalar(
            select(models.Submission).where(
                models.Submission.task_id == task_id,
                models.Submission.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        out = schemas.SubmissionOut.model_validate(existing)
        return out.model_copy(update={"duplicate_of": existing.id})

    await session.refresh(submission)
    return submission


@app.get("/tasks/{slug}/stats", response_model=schemas.StatsOut)
async def stats(slug: str, session: AsyncSession = Depends(get_session)):
    task = await _get_task(slug, session)

    total = await session.scalar(
        select(func.count()).select_from(models.Submission).where(models.Submission.task_id == task.id)
    )
    if not total:
        return schemas.StatsOut(
            task_id=task.id, slug=task.slug, submissions=0, passed=0,
            pass_rate=0.0, mean_score=0.0, p95_latency_ms=None, failures_by_check=[],
        )

    passed = await session.scalar(
        select(func.count()).select_from(models.Submission).where(
            models.Submission.task_id == task.id, models.Submission.passed.is_(True)
        )
    )
    mean_score = await session.scalar(
        select(func.avg(models.Submission.score)).where(models.Submission.task_id == task.id)
    )

    rows = list(
        await session.scalars(
            select(models.Submission).where(models.Submission.task_id == task.id)
        )
    )
    latencies = sorted(r.latency_ms for r in rows if r.latency_ms is not None)
    p95 = None
    if latencies:
        # Ближайший ранг: на 3 замерах «интерполированный p95» рисует число,
        # которого никто не наблюдал. Здесь всегда возвращается реальный замер.
        idx = max(0, math.ceil(0.95 * len(latencies)) - 1)
        p95 = latencies[idx]

    failures: dict[str, int] = {}
    for row in rows:
        for check in row.checks:
            if not check.get("passed"):
                failures[check["kind"]] = failures.get(check["kind"], 0) + 1

    return schemas.StatsOut(
        task_id=task.id,
        slug=task.slug,
        submissions=total,
        passed=passed or 0,
        pass_rate=round((passed or 0) / total, 4),
        mean_score=round(float(mean_score or 0.0), 4),
        p95_latency_ms=p95,
        failures_by_check=[
            schemas.FailureBreakdown(kind=k, failures=v)
            for k, v in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    )
