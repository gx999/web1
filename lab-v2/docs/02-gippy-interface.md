# 지피(운영총괄) ↔ 연구소 원장 인터페이스 설계안

상태: **설계안**. 지피 측 실제 연결은 미확인이다(01-first-report 3절 시험 필요).
원칙: 모든 응답은 원장 기록에서만 만든다. 지피가 상태를 '완료'로 쓰는 도구는 없다
(완료는 결과 저장이 확인된 연구원 실행만 만들 수 있다).

## 도구 (MCP 도구 이름 / HTTP 경로는 같은 의미)

| 도구 | 권한 | 동작 |
|---|---|---|
| `lab_status` | 읽기 | 프로젝트별 현재 단계, 실행 중 과제, 마지막 실제 활동 시각, 대기·차단 수, 다음 관리 실행 시각(알 때만), 사용량 신호(`allowed/limited/unknown`+재설정 시각) |
| `lab_list_tasks(project?, status?)` | 읽기 | 과제ID·단계·역할·상태·시도·실행ID·임대·오류분류·재시도 가능 여부·다음 행동 |
| `lab_get_task(id)` | 읽기 | 체크포인트, 사건 이력, 결과 요약 |
| `lab_get_artifact(project, version?)` | 읽기 | 판본 관계(초안 v0 → 검토 → 수정 v1 → 재검토), 판본 해시, 주장별 검토 판정, 출처 목록(확보/검색만/접근제한, 원문 링크, 문서 날짜와 날짜 종류, 확인 시각), 미확인 사항 |
| `lab_decisions_needed` | 읽기 | 사용자 판단·승인 대기 목록(공개·투고·외부 전송·비용·권한·수정 한도 초과·접근 제한 자료의 대체 여부) |
| `lab_create_goal(title, scope, priority)` | 쓰기(승인 범위) | 연구 목표 등록 → 첫 수집 과제 생성. 범위는 사용자가 승인한 분야 목록 안이어야 함 |
| `lab_create_task(project, stage, brief, depends_on)` | 쓰기(승인 범위) | 과제 추가(멱등 ID) |
| `lab_set_priority(id, priority)` | 쓰기 | 우선순위 변경(CAS: expected_rev 필요, 오래된 화면 덮어쓰기 차단) |
| `lab_request_assignment(id, role?)` | 쓰기 | 배정 요청. 실제 배정은 관리자 tick이 동시성·한도를 확인해 수행 |
| `lab_request_retry(id)` | 쓰기 | `failed`/`transient` 과제만 재대기. `blocked`(접근 제한)는 재시도 대신 대체 출처 과제를 만들거나 사용자 판단으로 넘김 — 우회 금지 |
| `lab_request_approval(kind, target, reason)` | 쓰기 | 승인 **요청** 기록만 생성. 실행 아님 |

수정·재검토 연결은 지피 도구가 아니라 관리자가 검토 결과(`changes_requested`)에서 자동 생성한다. 지피는 결과를 조회해 보고한다.

## 지피가 할 수 없는 것 (구조적으로 도구 없음)
- 과제를 `done`으로 표시, 검토를 통과로 표시, 결과물 공개·투고·외부 전송, 결제·자동충전, 인증·권한 변경.

## 알림
연구소 → 지피 푸시 경로는 확인되지 않았다. 지피는 ChatGPT 예약 작업(예: 08시 보고)이나 사용자 요청 시 `lab_status`·`lab_decisions_needed`를 조회한다. 보고에는 원장의 `generated_at`을 함께 적어 오래된 정보를 구분한다.

## 보고 형식 규칙 (실제 시험에서 추가)
- 지피의 모든 상태 보고는 `observed_at`(원장 조회 시각)과 **원본 상태값 목록 + 합계**를 함께 낸다. 상태를 묶어 세지 않는다(시험 3단계에서 checkpointed를 running으로 보고한 사례).
- 예약 실행으로 한 작업은 예약 ID·`last_run_time`과 원장 기록 시각을 함께 남긴다.
