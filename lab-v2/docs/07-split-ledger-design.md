# ③ 분리형 원장 설계 (Notion 지시함 + 비공개 GitHub 공식 기록)

상태: 설계·모의시험 단계. 비공개 저장소와 Notion 지시함은 사용자 준비(1~3단계) 후 생성한다.

## 역할별로 쓰는 곳 (실제 확인된 능력만 사용)
| 주체 | 쓰기 | 읽기 |
|---|---|---|
| 지피 | Notion 「연구 지시함」(요청·승인요청 행) | 비공개 저장소의 `status/` 요약 |
| Claude 관리자(예약·이벤트) | 비공개 저장소 전체(공식 기록) | Notion 지시함 |
| Claude 연구원 | 없음 — 관리자에게 `send_message` 보고만 | 배정 시 받은 자료 |
| 사용자 | Notion 지시함의 '승인' 열(사용자만 사용) | 둘 다 |

## 비공개 저장소 구조 (`gx999/daeyong-lab` 가정)
```
ledger/tasks/<project>/<task_id>.json     과제 상태(rev 필드로 낙관적 동시성)
ledger/events/YYYY-MM-DD.jsonl            사건(event_id로 중복 제거, 추가만)
sources/<project>/<source_id>.json        출처 메타(URL·문서 날짜와 종류·확인 시각·sha256·상태)
sources/<project>/<source_id>.txt         원문 발췌·추출문
drafts/<project>/v<N>.md                  초안 판본(파일명의 판본 + 본문 sha256을 tasks에 기록)
reviews/<project>/v<N>.json               주장별 판정·수정 요구
inbox_seen/<notion_row_id>.json           처리한 지시함 행(같은 행을 두 번 처리하지 않음)
status/summary.json                       지피·사용자용 요약(observed_at, 원본 상태값 목록+합계)
status/decisions_needed.json              사용자 판단·승인 대기 목록
```
동시성: 관리자는 쓰기 전 `git pull --rebase` → 변경 → `git push`. 푸시가 거절되면(다른 실행이 먼저 씀) 다시 받아 rev를 비교한 뒤 재시도한다(최대 3회). 이것이 원장 CAS 역할을 한다.

## Notion 「연구 지시함」 열
| 열 | 값 | 누가 |
|---|---|---|
| 요청ID | 제목 | 지피 |
| 종류 | `new_goal` / `new_task` / `set_priority` / `retry` / `approval_request` / `question` | 지피 |
| 내용 | 자연어 지시 | 지피 |
| 대상 | 프로젝트·과제 ID(있으면) | 지피 |
| 우선순위 | 1~5 | 지피 |
| 범위 확인 | 승인된 연구 분야 안인지 지피 판단(관리자가 다시 검사) | 지피 |
| 처리 상태 | `new` → `accepted` / `rejected` / `needs_owner` | 관리자가 읽어 판단 → **기록은 저장소 `inbox_seen/`** (Claude는 Notion 무인 쓰기 불가) |
| 사용자 승인 | 체크박스 — 공개·투고·외부 전송·비용·권한 | **사용자 본인만** |

주의: 지피와 사용자가 같은 Notion 계정을 쓰므로 '사용자 승인' 체크박스가 사용자 본인 입력임을 기술적으로 증명할 수 없다. 따라서 승인 대상 행동(공개·투고·외부 전송·비용·권한)은 체크박스만으로 실행하지 않고, **Claude 앱의 승인 카드나 사용자 직접 지시**가 있을 때만 실행한다. 체크박스는 '사용자 검토 요청됨' 표시로만 쓴다.

## 관리자 1회 실행 순서 (매시 예약 + 연구원 완료 알림 시)
1. 사용량 신호 확인(`rate_limit_info`) — limited면 `waiting_limit` 기록 후 종료.
2. 비공개 저장소 pull.
3. Notion 지시함 읽기 → 처리 안 한 행만 → 규칙 검사(`kernel/inbox.py`) → 승인 범위 안이면 과제 생성, 승인 대상이면 `decisions_needed`에 추가, 범위 밖이면 거절 사유 기록.
4. `kernel/manager.py` tick: 임대 회수, 한도 대기 해제, 배정(동시 실행 상한), 다음 단계 생성.
5. 연구원 세션 생성(배정된 과제만).
6. `status/summary.json` 갱신(공개 투영과 별개 — 비공개 요약).
7. 커밋·푸시(실패 시 재시도), 종료.

## 공개 현황판(홈페이지)
`kernel/projection.py`의 허용 목록 투영만 공개한다. 비공개 저장소의 내용은 공개 쪽으로 가지 않는다. 연동 방식은 홈페이지 결정 때 확정한다.
