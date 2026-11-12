"""공개 상태 파일 게시(공개 저장소 gx999/daeyong-lab-status의 status.json).

- 내용은 `public_state.build`(허용 목록 + 안전 검사)만 쓴다. 다른 파일은 올리지 않는다.
- 바뀐 것이 없으면 쓰지도, 커밋하지도 않는다(화면의 '변경 없음' 응답이 유지되도록).
- revision은 직전 게시본보다 항상 1 크다(화면은 줄어든 revision을 무시).
- 게시는 연구에 딸린 부수 작업이다. 실패해도 예외를 올리지 않고 결과에 기록만 한다(연구·원장 기록을 막지 않음).
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from .public_state import assert_lab_state_safe, build

FILE = "status.json"
VOLATILE = ("revision", "serverTime")


def _core(body: dict) -> str:
    return json.dumps({k: v for k, v in body.items() if k not in VOLATILE}, ensure_ascii=False, sort_keys=True)


def _git(dest: str, *a: str, run: Callable = subprocess.run) -> Any:
    return run(["git", "-C", dest, *a], capture_output=True, text=True, timeout=120)


def _epoch(t) -> float | None:
    try:
        import datetime as dt
        return dt.datetime.strptime(str(t), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def publish(root: str, dest: str, now: float, push: bool = False, include_tests: bool = True,
            run: Callable = subprocess.run, min_interval: float = 0) -> dict:
    """min_interval: 직전 게시(serverTime)로부터 이 초가 지나지 않았으면 이번 변경은 미룬다(다음 게시에 합쳐짐).
    몇 초 간격의 연속 푸시가 GitHub Pages 배포를 취소·지연시키는 것을 막기 위함. 실행 마지막 게시는 0으로 호출해 반드시 반영."""
    out: dict = {"changed": False}
    try:
        if push:   # 이 저장소는 관리자 실행만 쓴다(관리자 잠금 안에서). 원격을 기준으로 맞춘 뒤 쓴다.
            _git(dest, "fetch", "-q", "origin", "main", run=run)
            _git(dest, "reset", "-q", "--hard", "origin/main", run=run)
        p = os.path.join(dest, FILE)
        prev = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
        body = build(root, now, revision=0, include_tests=include_tests)
        if prev and _core(prev) == _core(body):
            out["revision"] = prev.get("revision")
            return out
        last = _epoch((prev or {}).get("serverTime"))
        if min_interval and last is not None and 0 <= now - last < min_interval:
            out.update(deferred=True, revision=(prev or {}).get("revision"))
            return out
        body["revision"] = int((prev or {}).get("revision", 0)) + 1
        assert_lab_state_safe(body)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, separators=(",", ":"))
        out.update(changed=True, revision=body["revision"], bytes=os.path.getsize(p))
        if push:
            _git(dest, "add", FILE, run=run)
            c = _git(dest, "commit", "-qm", f"status: rev {body['revision']}", run=run)
            r = _git(dest, "push", "-q", "origin", "HEAD:main", run=run)
            out["pushed"] = getattr(r, "returncode", 1) == 0 and getattr(c, "returncode", 1) == 0
            if not out["pushed"]:
                out["error"] = (getattr(r, "stderr", "") or getattr(c, "stderr", ""))[:160]
    except Exception as exc:  # noqa: BLE001 — 게시 실패가 연구를 막지 않게
        out["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out
