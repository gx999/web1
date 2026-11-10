# 24시간 무인 시험 실행 절차서 (준비 완료·미시작, 2026-11-10)

이 문서는 `08-24h-test-plan.md`를 대체한다. **예약은 아직 만들지 않았다.** 아래 "시작 전 결정" 이 모두 끝나야 만든다.
표기: [실제]=이 계정에서 확인, [모의]=모의시험만, [미확인]=증거 없음.

## 1. 역할 (2026-11-10 사용자 지시)
| 주체 | 맡는 일 | 쓰는 곳 | 읽는 곳 |
|---|---|---|---|
| 지피 | 연구 의제·우선순위·작업 지시, 결과의 근거·논리 검토, 사용자 보고 | Notion 「연구 지시함」 | 비공개 저장소 `status/` |
| Claude Code | 실행 시스템 구축·유지, 연구원 호출·단계 연결, 비용·보안 통제, 장애 복구, **단일 원장**·상태 기록 | 비공개 저장소 전체(유일한 작성자) | Notion 지시함(읽기만) |
| 연구원(OpenAI 세션·Claude 세션) | 배정된 단계 하나 | 없음(결과는 관리자가 기록) | 받은 원문 |
| 사용자 | 방향 최종 판단, 비용·권한·공개 승인 | — | 둘 다 |
연구 방향의 최종 판단은 지피와 사용자가 한다. 논문 초안 채택·수정(P1 D1~D5)과 홈페이지 연동은 이번 범위 밖.

## 2. 현재 구성과 기록 경로 [실제]
| 항목 | 값 |
|---|---|
| 실행 환경 | Claude Code 클라우드 환경 `web1 연구소 v2`(개인용). 네트워크 제한형: 공식 출처·OpenAI 문서 호스트만 허용. 비밀값 `OpenAI 연구소 v2 프로젝트 키` → api.openai.com 요청에만 프록시가 주입(코드·원장에 키 없음) |
| 코드 | 공개 저장소 `gx999/web1` 브랜치 `claude/sharp-brown-8lieqe`의 `lab-v2/` |
| 원장 | 비공개 저장소 `gx999/daeyong-lab` main — 아래 경로 |
| 지시함 | Notion 「연구 지시함」(collection 6ffdf2a6…). 현재 행 1개 = `I-0-시험`(question, 시험) — **실질 지시 0건** |

| 원장 경로 | 내용 |
|---|---|
| `ledger/tasks/<project>/` | v2 과제(P1 8건: done 7, `P1:user_review:v2` waiting_user) |
| `ledger/jobs/<J-요청ID>.json` | OpenAI 연구원 작업(단계별 상태·세션 ID·호출 ID) |
| `jobs/<J-…>/` | 작업 산출물(수집 원문 `sources/`, `draft.md`, `review.md`, `revise.md`) |
| `status/directives/<요청ID>.json`, `index.json` | 지시별 상태·근거·결과 위치(지피가 읽는 곳) |
| `status/summary.json` | 전체 현황. v2 과제·지시·OpenAI 작업·시험·외부 관측을 **별개 항목**으로 둠 |
| `ops/budget_openai_agents.json` | OpenAI 내부 예산 원장(예약·정산·정지). 누적 $0.094708(시험 2회) |
| `ops/openai_gate.json` | **없음** → OpenAI 유료 호출 차단 중 |
| `ops/external_observations.json` | v1 '최근 침해사고 대응 연구: blocked'(지피 전달, Claude 미검증) — v2와 합치지 않음 |
| `ops/manager_lock.json` | 관리자 실행 잠금(생성 전) |
| `tests/agents_pilot/` | 시험 AP1 2회 원 기록 + `CORRECTIONS.json`(원 기록은 수정 안 함) |
| `ledger/events/YYYY-MM-DD.jsonl` | 실행·잠금·요약 사건(맥 종료 대조 근거) |

## 3. 지시함 사용 규칙 (지피용)
1. 새 행을 만든다. **요청ID는 지피가 정하고 다시 쓰지 않는다**(예 `G-20261111-01`). 같은 ID를 다른 행에 쓰면 거절되고 `index.json`의 `rejected_rows`에 남는다.
2. 접수 후 행 내용을 고쳐도 다시 실행하지 않는다(기록에 `edited_after_intake`). 바꿀 일이 있으면 새 행.
3. OpenAI 연구원 단계 연결(수집→초안→독립 검토→수정)을 원하면: 종류 `new_task`, 대상 `openai_chain`, 내용 마지막 줄에
   `조문: 개인정보 보호법 제34조; 정보통신망 이용촉진 및 정보보호 등에 관한 법률 제48조의3` 형식. 현재 수집기는 법령 조문만 지원.
4. 시험이면 `시험` 체크. 시험 결과는 연구 성과로 기록되지 않는다.
5. 공개·투고·외부 전송·비용·인증·권한 관련 문구는 실행하지 않고 `needs_owner`로 둔다(체크박스는 승인 증거가 아님).
6. 결과는 `status/directives/index.json` → `status/directives/<요청ID>.json`의 `state`, `history`, `evidence`(원문 경로·해시·수집 상태), `results`(초안·검토·수정본 경로·해시·세션 ID)로 읽는다.

지시 상태(원본 값, 합치지 않음): `received` `accepted` `running` `done` `failed` `blocked` `needs_owner` `rejected` `answered` `waiting_user` `waiting_limit`.
`accepted`는 "접수됨"이지 실행됨이 아니다. OpenAI 호출이 차단된 동안에는 `accepted`에 머물고 `state_reason`에 차단 사유가 적힌다.

## 4. 예약 1회 실행 순서 (Claude 예약 세션, `tools/manager_run.py`)
```
0  get_session(자기) → rate_limit_info: isUsingOverage=true면 update_trigger(enabled=false)·알림·종료 / limited면 waiting_limit 기록·종료
1  git clone/pull gx999/daeyong-lab
2  lock --run <세션ID>             잠금 실패 → 아무것도 하지 않고 종료(다른 실행 진행 중)
3  plan-observe                    schedule_expired=true → update_trigger(enabled=false) → summary --push → unlock → 종료
4  Notion 지시함 조회(읽기) → rows.json → inbox --rows rows.json
5  (Claude 연구원 경로) get_session/list_sessions → obs.json → tick → create_session(to_launch) → adopt
6  run-openai --max-jobs 2          게이트 닫힘 → 작업은 accepted 유지 / 열림 → 중단 호출 복구 → 작업 실행
7  summary → unlock → 일괄 push(flush)
```
지피에게 알리는 방법: 별도 쓰기 없음. 지피가 정해진 시각에 `status/`를 읽는다(지피 예약 1시간 이상 간격).

## 5. 장애·재시작·중복 처리 [모의] (`tests/test_24h_sim.py`, `test_directives.py`, `test_openai_agents.py`)
| 상황 | 처리 |
|---|---|
| 실행 중 세션 종료(턴 진행 중) | 예약액 유지(잔여 예산에서 계속 차감). 다음 실행의 `recover_pending`이 턴 상태 조회 → 끝났으면 실제 usage로 정산, 진행 중이면 대기, 1시간 넘으면 취소 요청 |
| 세션 생성 응답 전 종료 | 1시간 뒤 최악값 정산(처리됐을 수 있음) |
| 단계 결과 유실(정산은 됐는데 결과 미기록) | 같은 단계를 새 시도 ID(`:a2`)로 1회만 재실행. 끝난 단계는 다시 하지 않음 |
| 잠금을 쥔 채 종료 | 잠금 50분 만료 → 다음 매시 실행이 인수 |
| 두 관리자 동시 실행 | 잠금 푸시가 먼저 된 쪽만 진행. 잠금 없이 같은 파일을 쓰면 푸시 거절(ConflictError) → 최신 상태로 다음 주기에 다시 판단 |
| 같은 행 재조회 | duplicate(아무것도 안 함). 사용량 지연(완료 직후 usage null) → 재조회 후 정산, 그래도 없으면 최악값 후 `reconcile` |
| 공식 출처 일시 오류 | 연결 실패·429·5xx만 최대 3회(3초·6초 대기) 재시도. 원인 `connect_failed/rate_limited/server_error/access_denied/not_found` 기록. 403 등 접근 거부는 재시도·우회 안 함 |
| 자료 일부 없음 | 미확보 목록(원인 포함)을 연구원에게 주고 해당 주장·비교는 "보류(자료 미확보)". 확보 0건이면 연구원 호출 없이 `blocked` |
| 작업 하나의 프로그램 오류 | 그 작업만 `failed`, 다른 작업 계속 |
| 종료 시각 경과 | 매 실행이 `end_at` 확인 → 예약 끄기, 이후 실행은 아무 작업 없이 종료 |

## 6. 예약 설정안 (생성 전, 사용자 승인 필요)
- 이름 `연구소v2 24시간 무인시험`, 매시(분 지터), **매번 새 세션**, 환경 `web1 연구소 v2`, 커넥터 Notion(읽기만 사용), 알림 push.
- 종료 장치 3중: ① 매 실행이 `ops/schedule.json`의 `end_at`(시작+24h)을 보고 자기 예약 끄기 ② 별도 1회 예약(end_at+5분)이 시험 예약 끄기 ③ 사용자 "예약 중지".
- 생성 직후 `get_trigger`로 활성·다음 실행·프롬프트 확인해 `ops/schedule.json`에 기록. 끝난 뒤 `enabled=false` 재확인.
- 비용: Claude는 구독 사용량(추가 사용량 꺼짐 — 사용자 확인 2026-11-10, 매 실행 `isUsingOverage` 감시). 24회 × 짧은 관리자 세션. OpenAI는 게이트가 열린 경우에만, 내부 예산 $3·하루 30회·동시 2.

## 7. 확인 방법 — "예약이 있다" ≠ "연구가 됐다"
| 확인하고 싶은 것 | 근거 |
|---|---|
| 예약이 존재·활성 | `get_trigger`(enabled, next_run_at) — 연구 여부와 무관 |
| 실제로 실행됐는가 | `ledger/events`의 `manager_lock`·`manager_run` 사건(세션 ID·시각), 예약 last_run |
| 맥 종료 중 실행 | 사용자가 알려 준 맥 종료~켜짐 시각과 위 사건 시각 대조. 알려 주지 않으면 "맥 종료 상태 미확인" |
| 연구가 진행됐는가 | `status/directives/*.json`의 `history`·`results`(파일 경로·해시·OpenAI 세션 ID) — 지시가 없으면 연구 0건이 정상 |
| 비용 | 내부 원장 + OpenAI 사용량 화면(반영 지연) + 크레딧 잔액 화면 |

## 8. 시작 전 결정 (사용자)
1. OpenAI: 크레딧이 적용되는 조직·프로젝트, 잔액·만료, 자동충전 꺼짐, 결제수단 유무, 프로젝트 강제 지출 한도 값 → 확인되면 `ops/openai_gate.json` 작성(확인 일시·확인자 기록). 강제 한도도 반영 지연이 있어 **추가 청구 0을 보증하지 않는다.**
2. 24시간 예약 생성(6절) 승인.
3. 시험 지시: 지피가 지시함에 `시험` 체크 행 1~2개 작성(3절 형식). 지시가 없으면 시험은 "실행 기록만 남는 무인 운전" 확인이 된다.
4. 맥 종료 시각 알려 주기(선택).

## 9. OpenAI Agents 자체 예약(automations)을 무인 운영에 쓰지 않는 이유
- 내부 비용 차단 코드(사전 예약·호출 수·중복 방지·정지 원장)가 그 안에서는 실행되지 않는다. 남는 것은 OpenAI 쪽 강제 한도(반영 지연)뿐.
- 결과가 우리 원장으로 자동으로 오지 않는다(웹훅에는 공개 수신 서버가 필요 → 새 호스팅 승인 필요).
- 대안: 지금처럼 Claude 예약 세션이 OpenAI 호출을 감싼다(채택안). 또는 자동화를 '수동 호출(invocations)'만 허용하고 Claude 관리자가 예산 검사 뒤 호출 — 자동화 자체의 비용 상한 필드가 문서에 없어 보류.

## 10. 남은 위험
- 예약 세션에서 비공개 저장소 `daeyong-lab` 접근은 첫 무인 실행에서 확인해야 함 [미확인] (환경의 저장소 목록에는 web1만 있음, 이 세션은 add_repo로 추가했었음).
- 환경의 OpenAI 비밀값은 이 환경의 **모든 세션**(Claude 연구원 세션 포함)에 주입된다. 연구원 프롬프트로 금지하지만 기술적 차단은 아님 → Claude 연구원은 비밀값 없는 별도 환경에서 띄우는 안을 결정 필요.
- 무인 Notion 쓰기는 조직 정책상 승인 필요 → 무인 실행은 Notion을 읽기만 한다(지피는 GitHub `status/`로 결과 확인).
- Agents API는 베타. 하네스 기본 지시로 턴당 입력이 약 1만 토큰(실측). 턴별 출력 상한 필드 없음 → 예약액 초과 시 취소로 대응.
- law.go.kr 연속 요청 시 연결 실패 실측(일시) → 재시도로 완화, 지속되면 `transient_failed`로 남음.
