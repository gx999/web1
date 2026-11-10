"""연구소 v2 운영 핵심의 자료 모델.

상태·단계·오류 분류는 모두 열거형으로 고정한다. 화면이나 보고서는 이 값만
읽으며, 기록에 없는 진척을 만들어 내지 않는다.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from typing import Any


class Status(str, enum.Enum):
    QUEUED = "queued"              # 선행조건 충족 시 배정 가능
    RUNNING = "running"            # 실행 세션에 배정됨(임대 유효)
    DONE = "done"                  # 결과 저장·확인 완료 (종결)
    FAILED = "failed"              # 재시도 한도 초과 등 (종결, 사람 확인)
    BLOCKED = "blocked"            # 접근 제한·권한 문제 — 우회하지 않고 보류
    WAITING_LIMIT = "waiting_limit"  # 사용량 한도 대기 — 자동 과금 전환 없음
    WAITING_USER = "waiting_user"  # 사용자 승인·판단 필요
    CANCELLED = "cancelled"


TERMINAL = {Status.DONE, Status.FAILED, Status.CANCELLED}


class Stage(str, enum.Enum):
    COLLECT = "collect"            # 자료 수집
    LITERATURE = "literature"      # 선행연구 검토
    PROBLEM = "problem"            # 연구 문제 도출
    DESIGN = "design"              # 연구 설계
    DRAFT = "draft"                # 분석·초안 작성
    REVIEW = "review"              # 독립 검토
    REVISE = "revise"              # 수정
    USER_REVIEW = "user_review"    # 사용자 검토 (자동 진행 종점)


PIPELINE = [Stage.COLLECT, Stage.LITERATURE, Stage.PROBLEM, Stage.DESIGN, Stage.DRAFT, Stage.REVIEW]

# 역할: 실제 실행 문맥과 분리된 '담당 책임'. 같은 역할도 매 실행마다 새 세션이다.
STAGE_ROLE = {
    Stage.COLLECT: "collector",
    Stage.LITERATURE: "collector",
    Stage.PROBLEM: "lead",
    Stage.DESIGN: "lead",
    Stage.DRAFT: "writer",
    Stage.REVIEW: "reviewer",
    Stage.REVISE: "writer",
    Stage.USER_REVIEW: "owner",
}


class ErrorClass(str, enum.Enum):
    TRANSIENT = "transient"        # 시간초과·5xx·연결 재설정 → 제한된 재시도
    LIMIT = "limit"                # 구독 사용량 한도 → 한도 대기
    ACCESS = "access"              # 403·로그인·CAPTCHA·해외 차단 → 보류(우회 금지)
    PERMISSION = "permission"      # 도구·권한 거부 → 사용자 판단
    FATAL = "fatal"                # 잘못된 입력 등 → 실패


class SourceStatus(str, enum.Enum):
    PENDING = "pending"
    RETRIEVED = "retrieved"              # 원문 또는 추출문 확보
    SEARCH_ONLY = "search_only"          # 검색 결과(스니펫)만 확인 — 원문 확인 아님
    ACCESS_RESTRICTED = "access_restricted"
    TRANSIENT_FAILED = "transient_failed"
    SHELL_ONLY = "shell_only"            # 접속은 됐으나 본문 없는 껍데기(예: law.go.kr 프레임 1,279B) — 원문 확보 아님


MIN_BODY_BYTES = 4000  # 이보다 작고 기대 표지가 없으면 껍데기로 본다(실제 시험 근거)


def judge_retrieval(http: int, body: bytes, must_contain: list[str]) -> SourceStatus:
    """접속 성공과 원문 확보를 구분한다. 기대 문구(예: '제34조')가 있어야 원문으로 인정."""
    if http in (401, 403, 451) or http == 0:
        return SourceStatus.ACCESS_RESTRICTED
    if http >= 500:
        return SourceStatus.TRANSIENT_FAILED
    text = body.decode("utf-8", errors="ignore")
    if must_contain and all(m in text for m in must_contain):
        return SourceStatus.RETRIEVED
    if len(body) < MIN_BODY_BYTES or must_contain:
        return SourceStatus.SHELL_ONLY
    return SourceStatus.RETRIEVED


@dataclasses.dataclass
class Source:
    id: str
    url: str
    kind: str                        # statute / case / agency / paper / press …
    status: SourceStatus = SourceStatus.PENDING
    checked_at: str | None = None    # 확인 시각(수집 시각)
    doc_date: str | None = None      # 문서 자체의 날짜(공포·시행·선고·발표일 구분은 date_kind)
    date_kind: str | None = None     # promulgated / effective / decided / announced / published
    sha256: str | None = None
    note: str = ""


@dataclasses.dataclass
class Task:
    id: str
    project: str
    stage: Stage
    status: Status = Status.QUEUED
    role: str = ""
    depends_on: list[str] = dataclasses.field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None   # 실행 세션 ID
    lease_until: float | None = None
    checkpoint: dict[str, Any] = dataclasses.field(default_factory=dict)
    artifact_version: int = 0        # 이 과제가 다루는 판본
    artifact_hash: str | None = None
    result: dict[str, Any] = dataclasses.field(default_factory=dict)
    error_class: ErrorClass | None = None
    error_detail: str = ""
    resume_at: float | None = None   # 재시도·한도 재확인 시각
    test: bool = False               # 시험 과제 여부 — 연구 성과 집계에서 제외
    rev: int = 0                     # 낙관적 동시성 제어용 판번호

    def __post_init__(self) -> None:
        if not self.role:
            self.role = STAGE_ROLE[self.stage]


def content_hash(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()
