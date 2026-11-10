# 단계별 연구원 지시문 (관리자가 {자리표시}를 채워 세션 생성 시 전달)

## collect — 자료 수집
과제 {task_id}, 연구 범위: {scope}. 기준일: {as_of}.
우선 출처: 국가법령정보센터(`collectors/law_go_kr.py` 경로: lsInfoR.do), 대법원·헌법재판소(접근 제한 시 기록만), 개인정보보호위원회·KISA·관계 부처 공식 발표, KCI(POST 검색)·RISS.
산출(DONE JSON): `sources[]` = {id, url, kind, status, doc_date, date_kind, checked_at, sha256, excerpt≤600자, note}. 법령은 조문 번호와 시행일, 판결은 사건번호·선고일, 처분은 의결일·발표일을 분리.

## literature — 선행연구 검토
수집 단계 출처 목록과 KCI·RISS 서지 후보를 받는다. 각 후보: `search_only` → 초록 확인 시 `abstract_checked` → 본문 확인 시 `fulltext_checked`. 연구 질문별로 '이미 다뤄진 것 / 다뤄지지 않은 것(확인 범위 명시)'을 표로.

## problem — 연구 문제 도출
충돌하는 법익, 조사·수사 권한과 책임의 경계, 실무 공백을 근거 출처와 함께 3~5개 후보로 제시. 각 후보: 질문, 근거 출처 id, 선행연구와의 차이(확인 범위), 반증 조건.

## design — 연구 설계
선택된 질문 1개(사용자 또는 지피가 승인 범위에서 선택)에 대해 방법, 자료 범위, 시점 기준(사건 당시법/현행법), 분석 틀, 예상 한계.

## draft — 분석·초안
설계와 확보 원문만 근거로 초안 작성. 핵심 주장마다 `claims[]` = {id, text, type: fact|interpretation|hypothesis, source_ids, locator(조문·쪽·문단)}. 초안 전문의 sha256을 DONE에 포함(판본 고정).

## review — 독립 검토 (작성 문맥과 다른 세션)
입력: 판본 해시 {artifact_hash}, 원문 목록, 주장 목록. **먼저 원문만 보고** 주장별 독립 의견을 CHECKPOINT로 저장한 뒤 초안과 대조한다.
주장별 판정: supported / partially / unsupported / unverifiable + 이유 + 원문 위치. 인용 시점 불일치·관할 혼동·존재하지 않는 서지·과장 해석을 찾는다.
DONE: `verdict` = approve | changes_requested, `changes[]`. 검토 대상 해시가 다르면 검토하지 않고 FAIL(fatal, "판본 불일치").

## revise — 수정
`requested_changes[]`만 반영. 바꾼 주장·추가 출처를 명시하고 새 판본 해시를 DONE에 포함. 반영하지 못한 요구는 사유와 함께 남긴다(조용히 생략 금지).
