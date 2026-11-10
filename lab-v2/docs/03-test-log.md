# 실행 기반 시험 기록 (2026-11-10)

모든 항목은 **시험 이벤트**이며 연구 성과가 아니다. 원장: Notion 비공개 '작업 대기열 (시험)'.
[실제] = 실제 클라우드 세션·실제 네트워크. [모의] = `tests/` 코드.

## 시험 1 — 작업자가 Notion에 직접 쓰기 [실제, 실패]
- A `session_01UTSPTcUW4t…`(container_015bD6NE…), B `session_01UTSPTdMyvj…` 1초 간격 생성·동시 실행.
- 둘 다 Notion 읽기 성공, **쓰기는 조직 MCP 정책(`mcpServerPolicy`) 승인 대기로 정지**. 무인 진행 불가.
- 관리자가 중단 후 두 과제를 `blocked`(permission)로 기록.

## 시험 2 — 작업자는 메시지로 보고, 관리자만 원장 기록 [실제]
| 과제 | 실행 | 결과 |
|---|---|---|
| T-C 수집 | `session_01UTSPTdbSot…`(container_01RTtfMv…) 03:54:26Z 시작 | S1 law.go.kr 200·**1,279B 껍데기(본문 아님)**, S2 대법원 **access_restricted**(502, 3회), S3 KISA 200·87,289B → 일부 출처 실패에도 나머지 저장, `done` |
| T-D 선행연구 시도1 | `session_01UTSPTaYC1U…`(container_01WwqXSE…) 같은 시각 시작 | OpenAlex `Rate limit exceeded`(외부 IP 공유 한도) → 값 추측 없이 중단, API 키(이메일 외부 전송) 미발급 |
| T-D 시도2 | `session_01UTSPTc2kGH…`(대체 출처 Crossref) | 체크포인트 3/6 전송 후 **관리자가 강제 중단** → 세션 IDLE 확인 |
| T-D 시도3 | `session_01UTSPTcKYoW…` 04:12:13Z | 재조회로 1~3번 DOI가 체크포인트와 같음을 먼저 확인 → 4~6번만 처리 → 04:12:51Z `done` 6/6 |

- **중단은 즉시가 아니었다** [실제]: 시도2 중단 요청(04:02:56Z 직후) 뒤 04:03:06Z에 4번 체크포인트가 도착. 원장은 중단된 실행의 늦은 기록을 채택하지 않아 4번은 시도3에서 다시 처리됨(서지 조회라 무해, 실제 중복 실행 1건). 쓰기 부작용이 있는 단계는 '중단 확인(IDLE) 후 재배정'과 멱등 키가 필수임을 확인.
- 품질 메모: 3·4번 동일 제목·다른 DOI(SSRN 중복 등록) → 검토 단계 확인 대상. 질의어가 영어라 국내 문헌 후보 0건 — KCI·RISS 검색 경로가 별도로 필요.

- 작업자 → 관리자 `send_message`는 **승인 프롬프트 없이 전달**됨(이벤트 기반 연결 경로로 사용 가능).
- 세션 메타데이터: `rate_limit_info.status=allowed`, `five_hour`, `isUsingOverage=false`. `usage.cost_usd`(예: 시도2 0.168)는 API 환산 표시로 보이며 **실제 청구액으로 해석하지 않음**(구독 경로, 잔량 미확인).

## 시험에서 얻은 수정 사항 (코드 반영)
1. 외부 출처 한도(`Rate limit exceeded`)를 구독 사용량 한도로 오분류하던 규칙 수정 → 일시 장애로 늦춰 재시도.
2. 접속 성공 ≠ 원문 확보: `judge_retrieval()` 추가 — 기대 문구(예: `제34조`)가 없으면 `shell_only`.
3. 국가법령정보센터 본문 경로 확인 [실제]: `https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=<seq>&chrClsCd=010202&urlMode=lsInfoR&efYd=<시행일>` → 505,383B, 「개인정보 보호법」 제34조 본문 추출 확인(lsiSeq 283839, 시행 2026. 9. 11.). `lsiSeq`·`efYd`는 `/법령/<법령명>` 페이지의 iframe 주소에서 얻는다. 키 불필요. 문서 날짜는 '시행일'로 기록.

## 시험 3 — 실제 과제 P1 전 단계 연결 [실제, 사람 감독 하 실행 · 무인 아님]
질문: 개인정보 보호법 §34 유출 통지·신고와 정보통신망법 §48의3 침해사고 신고의 이중 구조. 원고 본문은 비공개(Notion)에만 두고 여기에는 기록 메타데이터만 남긴다.

| 단계 | 세션(앞자리) | 판본 sha256(앞 8자) | 결과 |
|---|---|---|---|
| 수집 | `01UTSPTaqdVs` | — | 법령 조문 5 retrieved, KCI 서지 후보 5(search_only). 작업자 시각 미래값 → 서버 시각 기록 |
| 초안 v0 | `01UTSPTfHvv7` | 0dc8cfcf (1,289자) | 작성자 분량 보고 2,667자 ≠ 실측 — 관리자 재계산으로 발견 |
| 독립검토 v0 (원문 먼저→초안) | `01UTSPTbCXr9` | 0dc8cfcf 일치 | changes_requested R1~R7. 원문 독립 재수집 해시 일치 |
| 수정 v1 | `01UTSPTdmzp9` | d0468ea2 (2,409자) | 일부 반영 — 정통망 §76 원문 일시 실패 3회 → 미확인 유지(검토 요약 맹신 안 함) |
| 재검토 v1 | `01UTSPTcoXZP` | d0468ea2 일치 | changes_requested C1~C6. v0 검토자 요약 오류(§76②→④, 3천만→1천만) 정정 |
| 수정 v2 | `01UTSPTb5gMj` | 8f8801c0 (2,496자) | C1~C6 반영 |
| 재검토 v2 | `01UTSPTeHUqc` | 8f8801c0 일치 | **approve** — 사실 주장 24개 중 22 일치·1 부분(N1)·1 확인불가 |
| 사용자 검토 | — | 8f8801c0 | **대기** (자동 공개 없음) |

운영 관찰: 7개 단계 모두 서로 다른 세션. 판본 해시는 관리자가 매 단계 재계산해 고정. 상태 메시지 지연 실측: 관리자 프로세스 재시작 중 약 11분 지연 후 도착(유실 없음). 수정 상한 3회 중 2회 사용.

## 시험 4 — ③ 분리형 루프 1회 (사람 감독 하 관리 실행) [실제]
1. 지피가 Notion 「연구 지시함」에 `I-0-시험`(question) 작성 — Notion createdTime 07:09:11Z, Claude 조회로 확인.
2. 관리자 첫 조회는 **Notion API 요청 한도(rate_limited, 30초 대기)** — 외부 출처 일시 한도로 분류, 30초 후 1회 재시도 성공(우리 구독 한도와 구분).
3. `kernel/inbox.py` 규칙 → `answered`(상태 요약으로 답함). 비공개 저장소 `inbox_seen/<행ID>.json` 기록.
4. P1 기록(과제 8건, 판본 v0~v2 원고)을 비공개 저장소 원장으로 옮김 — 판본 해시 일치 확인. Notion 시험 원장은 보존.
5. `status/summary.json`(observed_at·원본 상태 목록·합계: done 7, waiting_user 1)과 `status/decisions_needed.json`(D1~D5) 생성 후 `main`에 푸시.
남은 확인: 지피가 `status/`를 읽어 보고하는 단계(사용자 경유).
