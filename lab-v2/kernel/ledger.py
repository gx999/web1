"""영속 원장(작업 대기열·사건 기록) 인터페이스와 파일 기반 참조 구현.

실제 운영 저장소(비공개 Git 저장소의 낙관적 push, 또는 Notion)는 같은
의미론을 지켜야 한다.
- 모든 변경은 `expected_rev` 조건부 갱신(CAS)이다. 오래된 화면·중복 실행이
  새 변경을 덮어쓰지 못한다.
- 종결 상태(done/failed/cancelled)는 되돌릴 수 없다.
- 사건은 `event_id`로 한 번만 기록한다(중복·재전송 무해).
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import threading
from typing import Any, Iterable

from .model import Source, SourceStatus, Stage, Status, Task, TERMINAL, ErrorClass


class ConflictError(Exception):
    """조건부 갱신 실패: 다른 실행자가 먼저 변경했다."""


class TerminalStateError(Exception):
    """종결 상태를 되돌리려는 시도."""


def _task_to_json(t: Task) -> dict[str, Any]:
    d = dataclasses.asdict(t)
    d["stage"] = t.stage.value
    d["status"] = t.status.value
    d["error_class"] = t.error_class.value if t.error_class else None
    return d


def _task_from_json(d: dict[str, Any]) -> Task:
    d = dict(d)
    d["stage"] = Stage(d["stage"])
    d["status"] = Status(d["status"])
    d["error_class"] = ErrorClass(d["error_class"]) if d.get("error_class") else None
    return Task(**d)


class Ledger:
    """파일 하나에 원자적으로 저장하는 참조 구현(시험·로컬 개발용)."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.tasks: dict[str, Task] = {}
        self.sources: dict[str, dict[str, Source]] = {}
        self.events: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()
        if path and os.path.exists(path):
            self._load()

    # ---- 영속화 -------------------------------------------------------
    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        self.tasks = {k: _task_from_json(v) for k, v in raw["tasks"].items()}
        self.sources = {
            tid: {sid: Source(**{**s, "status": SourceStatus(s["status"])}) for sid, s in srcs.items()}
            for tid, srcs in raw.get("sources", {}).items()
        }
        self.events = raw.get("events", [])
        self._event_ids = {e["event_id"] for e in self.events}

    def _flush(self) -> None:
        if not self.path:
            return
        raw = {
            "tasks": {k: _task_to_json(v) for k, v in self.tasks.items()},
            "sources": {
                tid: {sid: {**dataclasses.asdict(s), "status": s.status.value} for sid, s in srcs.items()}
                for tid, srcs in self.sources.items()
            },
            "events": self.events,
        }
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # 원자적 교체: 중간 실패 시 이전 판본 유지

    # ---- 과제 ---------------------------------------------------------
    def add(self, task: Task) -> Task:
        """같은 ID가 있으면 기존 것을 돌려준다(멱등 생성)."""
        with self._lock:
            if task.id in self.tasks:
                return self.tasks[task.id]
            self.tasks[task.id] = task
            self._flush()
            return task

    def get(self, task_id: str) -> Task:
        return dataclasses.replace(self.tasks[task_id])

    def all(self) -> list[Task]:
        return [dataclasses.replace(t) for t in self.tasks.values()]

    def update(self, task_id: str, expected_rev: int, **changes: Any) -> Task:
        with self._lock:
            cur = self.tasks[task_id]
            if cur.rev != expected_rev:
                raise ConflictError(f"{task_id}: rev {cur.rev} != expected {expected_rev}")
            if cur.status in TERMINAL and changes.get("status", cur.status) != cur.status:
                raise TerminalStateError(f"{task_id} is {cur.status.value}")
            new = dataclasses.replace(cur, **changes, rev=cur.rev + 1)
            self.tasks[task_id] = new
            self._flush()
            return dataclasses.replace(new)

    # ---- 출처(과제별로 분리 저장: 한 출처 실패가 다른 출처를 지우지 않는다) ----
    def put_source(self, task_id: str, src: Source) -> None:
        with self._lock:
            self.sources.setdefault(task_id, {})[src.id] = src
            self._flush()

    def sources_of(self, task_id: str) -> list[Source]:
        return list(self.sources.get(task_id, {}).values())

    # ---- 사건 ---------------------------------------------------------
    def record(self, event_id: str, **fields: Any) -> bool:
        """사건을 한 번만 기록. 이미 있으면 False(중복 무시)."""
        with self._lock:
            if event_id in self._event_ids:
                return False
            self._event_ids.add(event_id)
            self.events.append({"event_id": event_id, **fields})
            self._flush()
            return True

    def events_of(self, task_id: str) -> Iterable[dict[str, Any]]:
        return [e for e in self.events if e.get("task") == task_id]
