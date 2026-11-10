"""비공개 Git 저장소를 원장으로 쓰는 어댑터.

- 과제: ledger/tasks/<project>/<task_id>.json (파일 하나 = 과제 하나)
- 사건: ledger/events/YYYY-MM-DD.jsonl (추가만, event_id로 중복 제거)
- 동시성: 커밋 후 push가 거절되면(다른 실행이 먼저 씀) fetch+rebase 후 다시 push.
  같은 과제 파일을 양쪽이 바꿔 rebase 충돌이 나면 '나중 쓰기'를 버리고 원격을 따른다
  → 호출자는 ConflictError를 받아 최신 상태로 다시 판단한다(CAS와 같은 의미).
비밀값은 이 모듈에 두지 않는다. 인증은 세션의 git 프록시가 처리한다.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
from typing import Any

from .ledger import ConflictError, TerminalStateError, _task_from_json, _task_to_json
from .model import TERMINAL, Task

SAFE = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def _safe(name: str) -> str:
    return SAFE.sub("_", name)


class RepoLedger:
    def __init__(self, root: str, branch: str = "main", author: str = "Claude lab manager",
                 email: str = "noreply@anthropic.com", push: bool = True) -> None:
        self.root, self.branch, self.push_enabled = root, branch, push
        self.author, self.email = author, email

    # ---- git ----------------------------------------------------------
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True, check=check)

    def sync(self) -> None:
        self._git("fetch", "-q", "origin", self.branch)
        self._git("rebase", "-q", f"origin/{self.branch}")

    def _commit_push(self, paths: list[str], message: str, attempts: int = 3) -> None:
        self._git("add", "--", *paths)
        if not self._git("diff", "--cached", "--quiet", check=False).returncode:
            return  # 변경 없음
        self._git("-c", f"user.name={self.author}", "-c", f"user.email={self.email}",
                  "commit", "-q", "-m", message)
        if not self.push_enabled:
            return
        for _ in range(attempts):
            if self._git("push", "-q", "origin", f"HEAD:{self.branch}", check=False).returncode == 0:
                return
            self._git("fetch", "-q", "origin", self.branch)
            if self._git("rebase", "-q", f"origin/{self.branch}", check=False).returncode != 0:
                self._git("rebase", "--abort", check=False)
                self._git("reset", "-q", "--hard", f"origin/{self.branch}")
                raise ConflictError("원격에 같은 파일의 더 새로운 기록이 있음 — 최신 상태로 다시 판단")
        raise ConflictError("push 재시도 한도 초과")

    # ---- 과제 ---------------------------------------------------------
    def _path(self, t: Task | str, project: str | None = None) -> str:
        tid = t.id if isinstance(t, Task) else t
        proj = t.project if isinstance(t, Task) else (project or tid.split(":")[0])
        return os.path.join("ledger", "tasks", _safe(proj), _safe(tid) + ".json")

    def get(self, task_id: str) -> Task:
        p = os.path.join(self.root, self._path(task_id))
        with open(p, encoding="utf-8") as f:
            return _task_from_json(json.load(f))

    def all(self) -> list[Task]:
        base = os.path.join(self.root, "ledger", "tasks")
        out = []
        for d, _, files in os.walk(base):
            for fn in files:
                if fn.endswith(".json"):
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        out.append(_task_from_json(json.load(f)))
        return out

    @property
    def tasks(self) -> dict[str, Task]:
        """메모리 원장(Ledger.tasks)과 같은 조회 모양 — inbox 규칙이 대상 과제 존재를 확인할 때 사용."""
        return {t.id: t for t in self.all()}

    def _write(self, t: Task) -> str:
        rel = self._path(t)
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_task_to_json(t), f, ensure_ascii=False, indent=1, sort_keys=True)
        return rel

    def add(self, task: Task) -> Task:
        if os.path.exists(os.path.join(self.root, self._path(task))):
            return self.get(task.id)
        self._commit_push([self._write(task)], f"ledger: add {task.id}")
        return task

    def update(self, task_id: str, expected_rev: int, **changes: Any) -> Task:
        cur = self.get(task_id)
        if cur.rev != expected_rev:
            raise ConflictError(f"{task_id}: rev {cur.rev} != expected {expected_rev}")
        if cur.status in TERMINAL and changes.get("status", cur.status) != cur.status:
            raise TerminalStateError(f"{task_id} is {cur.status.value}")
        new = dataclasses.replace(cur, **changes, rev=cur.rev + 1)
        self._commit_push([self._write(new)], f"ledger: {task_id} rev{new.rev} {new.status.value}")
        return new

    # ---- 사건 ---------------------------------------------------------
    def record(self, event_id: str, day: str | None = None, **fields: Any) -> bool:
        if day is None:
            import datetime
            day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        rel = os.path.join("ledger", "events", f"{day}.jsonl")
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                if any(json.loads(line).get("event_id") == event_id for line in f if line.strip()):
                    return False
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event_id": event_id, **fields}, ensure_ascii=False) + "\n")
        self._commit_push([rel], f"event: {event_id}")
        return True

    # ---- 출처(과제별 파일, 한 출처 실패가 다른 출처를 지우지 않음) ----------------
    def put_source(self, task_id: str, src: Any) -> None:
        d = dataclasses.asdict(src)
        d["status"] = getattr(src.status, "value", src.status)
        rel = os.path.join("sources", _safe(task_id), _safe(src.id) + ".json")
        self.write_json(rel, d, f"source: {task_id}/{src.id}")

    def sources_of(self, task_id: str) -> list[Any]:
        from .model import Source, SourceStatus
        base = os.path.join(self.root, "sources", _safe(task_id))
        out = []
        if os.path.isdir(base):
            for fn in sorted(os.listdir(base)):
                if fn.endswith(".json"):
                    with open(os.path.join(base, fn), encoding="utf-8") as f:
                        d = json.load(f)
                    out.append(Source(**{**d, "status": SourceStatus(d["status"])}))
        return out

    def try_lock(self, owner: str, now: float, ttl: float = 50 * 60, rel: str = "ops/manager_lock.json") -> tuple[bool, dict]:
        """관리자 실행 잠금(원격 저장소 푸시로 판정). 다른 실행이 잠금을 잡고 있고 만료 전이면 False.
        푸시가 거절되면(동시에 잡으려 함) 원격 상태로 되돌리고 False — 같은 작업의 이중 실행 방지."""
        self.sync()
        p = os.path.join(self.root, rel)
        cur = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
        if cur and cur.get("owner") != owner and cur.get("released_at") is None and now < cur.get("expires_at", 0):
            return False, cur
        lock = {"owner": owner, "acquired_at": now, "expires_at": now + ttl, "released_at": None,
                "previous": {k: cur.get(k) for k in ("owner", "acquired_at", "released_at", "expires_at")} if cur else None}
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(lock, f, ensure_ascii=False, indent=1)
        self._git("add", "--", rel)
        self._git("-c", f"user.name={self.author}", "-c", f"user.email={self.email}", "commit", "-q", "-m",
                  f"lock: {owner}")
        if self._git("push", "-q", "origin", f"HEAD:{self.branch}", check=False).returncode != 0:
            self._git("fetch", "-q", "origin", self.branch)
            self._git("reset", "-q", "--hard", f"origin/{self.branch}")
            return False, json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        return True, lock

    def release_lock(self, owner: str, now: float, rel: str = "ops/manager_lock.json") -> None:
        p = os.path.join(self.root, rel)
        if not os.path.exists(p):
            return
        cur = json.load(open(p, encoding="utf-8"))
        if cur.get("owner") == owner:
            cur["released_at"] = now
            self.write_json(rel, cur, f"unlock: {owner}")

    def flush(self, attempts: int = 3) -> None:
        """일괄 모드(push=False)에서 모은 커밋을 한 번에 푸시. 거절 시 rebase 후 재시도."""
        for _ in range(attempts):
            if self._git("push", "-q", "origin", f"HEAD:{self.branch}", check=False).returncode == 0:
                return
            self._git("fetch", "-q", "origin", self.branch)
            if self._git("rebase", "-q", f"origin/{self.branch}", check=False).returncode != 0:
                self._git("rebase", "--abort", check=False)
                raise ConflictError("일괄 푸시 중 같은 파일 충돌 — 이번 실행 결과를 버리고 다음 주기에 다시 판단")
        raise ConflictError("push 재시도 한도 초과")

    def write_json(self, rel: str, obj: Any, message: str) -> None:
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        self._commit_push([rel], message)
