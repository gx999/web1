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
