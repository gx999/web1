# 24시간 시험 예약 지시문 (검토용 — 아직 생성하지 않음)

## A. 매시 관리자 실행 (예약 `연구소v2 24시간 무인시험`)
- cron: `47 * 11 11 *` (UTC) = 2026-11-11 09:47 KST ~ 11-12 08:47 KST, 24회. **날짜가 cron에 고정돼 있어 예약 자체가 11-12 이후에는 실행되지 않음**(내년 같은 날짜 전에 끄기 확인).
- 매번 새 세션, 환경 `web1 연구소 v2`(관리자 전용, OpenAI 키 주입), 모델 Sonnet, 커넥터 Notion(읽기만), 알림 push.

```
당신은 '정대용 디지털 연구소 v2'의 관리자 실행(24시간 무인 시험, test)입니다. 연구 판단은 하지 않습니다.
금지: Notion 쓰기, 공개 게시·외부 연락·홈페이지 변경, 예약 생성, 권한·결제 변경, 키·토큰 출력, 접근 제한 우회,
lab-v2 코드 수정, 기존 v1 연구소·P1 기록 변경. 의심되면 아무것도 하지 말고 아래 7단계(요약)만 하고 끝내세요.

0. mcp__claude-code-remote__get_session(인자 없음) → rate_limit_info. isUsingOverage=true면
   update_trigger(trigger_id=<ops/schedule.json의 trigger_id>, enabled=false) 후 종료.
   status=limited면 아래 1~2와 7만 하고 사유 'waiting_limit'로 끝냅니다.
1. git clone https://github.com/gx999/daeyong-lab /tmp/dl (실패하면 재시도 1회, 그래도 실패면 종료 — 다른 방법으로 우회하지 않음)
   git clone -b claude/sharp-brown-8lieqe https://github.com/gx999/web1 /tmp/w ; git -C /tmp/w checkout <생성 시 고정할 커밋> ; cd /tmp/w/lab-v2
   M="python tools/manager_run.py --repo /tmp/dl"
2. $M lock --run <자기 세션ID>  → acquired=false면 즉시 종료(다른 관리자 실행 중).
3. $M plan-observe → schedule_expired=true면: update_trigger(enabled=false) → get_trigger 결과를 /tmp/t.json에 저장 →
   $M schedule-record --trigger-json /tmp/t.json → 7단계로.
4. Notion query-data-sources(rows 모드, data_source_url "collection://6ffdf2a6-8193-4c47-8e71-6a1d6eed55db", limit 100)
   결과 JSON을 그대로 /tmp/rows.json 에 저장 → $M inbox --rows /tmp/rows.json
5. $M run-openai --run <자기 세션ID> --max-jobs 1
   (게이트·시험 상한·잠금 연장은 코드가 판단. 새 지시나 재개할 작업이 없으면 OpenAI 호출 0회로 끝남 — 정상)
6. get_trigger(trigger_id) 결과를 /tmp/t.json에 저장 → $M schedule-record --trigger-json /tmp/t.json
7. $M summary --run <자기 세션ID> ; $M unlock --run <자기 세션ID> ; cd /tmp/dl && git push origin HEAD:main
   (푸시 거절 시 git pull --rebase 후 1회 재시도. 충돌이면 그대로 종료 — 다음 실행이 다시 판단)
마지막 답변: 잠금 획득 여부, 지시 처리 결과, OpenAI 호출 수와 내부 비용, 예약 enabled 값, 오류.
```

## B. 종료 확인 1회 실행 (예약 `연구소v2 24시간 시험 종료 확인`)
- run_once_at: 2026-11-12T09:52:00[Asia/Seoul], 새 세션, 같은 환경.
```
'정대용 디지털 연구소 v2' 24시간 시험의 종료 확인입니다. 연구·OpenAI 호출·Notion 쓰기는 하지 않습니다.
1. ops/schedule.json(비공개 저장소 gx999/daeyong-lab)의 trigger_id를 읽고 update_trigger(enabled=false).
2. get_trigger 결과를 /tmp/t.json에 저장 → web1 lab-v2의 manager_run.py schedule-record 로 기록 → 푸시.
3. enabled가 false가 아니면 다시 켜거나 지우지 말고, 마지막 답변 첫 줄에 '종료 실패'와 응답을 적으세요(사용자 보고용).
```

## C. 사용자가 claude.ai 예약 화면에서 만드는 무인 리허설(1회) — 2026-11-10
도구(create_trigger)로 만든 예약에는 저장소·커넥터·원격 도구가 붙지 않음(실측). 화면에서 환경 `web1 연구소 v2`, 저장소 web1·daeyong-lab, 커넥터 Notion을 선택해 만든다.
```
정대용 디지털 연구소 v2 '무인 예약 리허설 2차'(1회용 기술 시험, 연구 아님). 사용자가 승인한 실행입니다.
금지: Notion 쓰기, 예약 생성·변경, add_repo 등 권한 추가, 권한 설정 변경, 코드 수정, 키·토큰 출력, 외부 연락, 공개 게시,
유료 OpenAI 호출(게이트 파일이 없어 코드가 자동 차단해야 정상). 막히면 우회하지 말고 기록만 하세요.

1. 환경 기록: `ls /home/user`, `date -u`. 사용 가능한 도구 중 Notion 도구와 mcp__claude-code-remote__ 도구가 있는지 기록.
   mcp__claude-code-remote__get_session 이 있으면 호출해 session_id·sources·rate_limit_info(isUsingOverage) 기록
   (isUsingOverage=true면 4~7을 하지 말고 8로). 없으면 RUN=run-$(date -u +%Y%m%dT%H%M%SZ) 를 실행 ID로 사용.
2. 원장 D=/home/user/daeyong-lab (없으면 `git ls-remote https://github.com/gx999/daeyong-lab HEAD` 오류 앞 120자 기록 후 8로)
   코드: `git -C /home/user/web1 fetch -q origin claude/sharp-brown-8lieqe && git -C /home/user/web1 worktree add /tmp/w 66c49e8`
   cd /tmp/w/lab-v2 ; M="python tools/manager_run.py --repo $D"
3. `$M lock --run $RUN` (acquired=false면 8로)
4. `$M plan-observe`
5. Notion 도구가 있으면 query-data-sources(rows 모드, data_source_url "collection://6ffdf2a6-8193-4c47-8e71-6a1d6eed55db", limit 100)
   결과 JSON을 /tmp/rows.json에 저장 → `$M inbox --rows /tmp/rows.json`. 없거나 승인 필요하면 실패로 기록.
6. `$M run-openai --run $RUN --max-jobs 1` (gate 문구, openai_calls 기록)
7. `$M summary --run $RUN` → `$M unlock --run $RUN`
8. 결과 표(단계, 성공/실패/거부, 근거 한 줄 — 권한 검사 거부면 문구 앞 150자)를
   $D/tests/probes/rehearsal2-$RUN.json 으로 저장하고 `cd $D && git add tests/probes ledger status ops && git commit -m "test: rehearsal2 $RUN" && git push origin HEAD:main`
   (거절 시 git pull --rebase 후 1회 재시도). 같은 표를 최종 답변에도 쓰세요.
```

## D. 24시간 무인 시험 매시 실행 (사용자가 화면에서 생성) — 승인 2026-11-10T13:07Z
- 이름 `연구소v2 24시간 무인시험`, 환경 `web1 연구소 v2`, 저장소 web1·daeyong-lab, 커넥터 Notion만, 모델 Sonnet.
- 일정: 사용자 정의 cron `47 * 11 11 *`(시간대 UTC) = 11-11 09:47 ~ 11-12 08:47 KST 24회. 사용자 정의가 안 되면 Hourly 후 관리자 세션이 종료 시각에 끔.
```
정대용 디지털 연구소 v2 '24시간 무인 시험' 매시 관리자 실행입니다(시험, test=true). 연구 판단은 하지 않습니다.
금지: Notion 쓰기, 예약 생성, add_repo 등 권한 추가, 권한·결제 설정 변경, 코드 수정, 키·토큰 출력, 외부 연락, 공개 게시,
홈페이지 변경, v1 연구소·P1 기록 변경. 막히면 우회하지 말고 기록만 하세요. OpenAI 호출은 아래 run-openai 명령으로만 합니다.

1. RUN=run-$(date -u +%Y%m%dT%H%M%SZ). `ls /home/user`, `date -u` 기록.
   D=/home/user/daeyong-lab (없으면 8로).
   `git -C /home/user/web1 fetch -q origin claude/sharp-brown-8lieqe && git -C /home/user/web1 worktree add /tmp/w feff2a1`
   cd /tmp/w/lab-v2 ; M="python tools/manager_run.py --repo $D"
2. `$M lock --run $RUN` → acquired=false면 8로(다른 실행 진행 중).
3. `$M plan-observe` → schedule_expired=true면: mcp__claude-code-remote__list_triggers가 있으면 이름이
   '연구소v2 24시간 무인시험'인 예약을 찾아 update_trigger(enabled=false) 후 get_trigger 결과를 /tmp/t.json에 저장 →
   `$M schedule-record --trigger-json /tmp/t.json`. 도구가 없으면 '종료 시각 경과, 끄기 도구 없음'을 기록. 그다음 7로.
4. Notion query-data-sources(rows 모드, data_source_url "collection://6ffdf2a6-8193-4c47-8e71-6a1d6eed55db", limit 100)
   결과 JSON을 /tmp/rows.json에 저장 → `$M inbox --rows /tmp/rows.json`. Notion 도구가 없거나 실패하면 기록하고 계속.
5. `$M run-openai --run $RUN --max-jobs 1` (새 지시·재개 작업이 없으면 호출 0회 — 정상)
6. list_triggers 도구가 있으면 같은 예약의 get_trigger 결과를 /tmp/t.json에 저장 → `$M schedule-record --trigger-json /tmp/t.json`
7. `$M summary --run $RUN` → `$M unlock --run $RUN`
8. 결과 요약(잠금, 지시 처리, openai_calls, 비용, 오류)을 $D/ops/runs/$RUN.json 으로 저장하고
   `cd $D && git add -A && (git commit -qm "run: $RUN" || true) && git push origin HEAD:main`
   (거절 시 git pull --rebase 후 1회 재시도, 충돌이면 그대로 종료). 같은 요약을 최종 답변에도 쓰세요.
```

- 2026-11-10 13:21Z: 고정 커밋 feff2a1(Notion 마크다운 정규화)로 사용자가 예약 지시문 수정. 화면에서 만든 예약은 관리 세션이 수정·끄기 불가(실행 세션 자신은 끄기만 가능).

## E. 운영 매시 실행(초안 — 24시간 시험 종료·사용자 승인 후 기존 예약 지시문 교체)
- 같은 예약(매시 :12) 유지, 저장소 web1·daeyong-lab·**daeyong-lab-status**, 커넥터 Notion만, 모델 Sonnet.
- `<COMMIT>`은 승인 시점의 lab-v2 커밋으로 고정(운영: 1d4d174 → 5026d68).
```
정대용 디지털 연구소 v2 매시 관리자 실행입니다. 연구 판단은 하지 않습니다.
금지: Notion 쓰기, 예약 생성·변경, add_repo 등 권한 추가, 권한·결제 설정 변경, 코드 수정, 키·토큰 출력, 외부 연락,
공개 게시(아래 publish-state·run-openai의 공개 상태 파일 푸시만 허용), 홈페이지 변경, v1 연구소·P1 기록 변경.
막히면 우회하지 말고 기록만 하세요. OpenAI 호출은 아래 run-openai 명령으로만 합니다.

0. ToolSearch로 "select:mcp__claude-code-remote__get_session" 를 불러와 get_session(인자 없음) → rate_limit_info 기록.
   isUsingOverage=true 또는 status가 allowed가 아니면 사유 'waiting_limit'로 8만 하고 끝냅니다.
1. RUN=run-$(date -u +%Y%m%dT%H%M%SZ). D=/home/user/daeyong-lab ; P=/home/user/daeyong-lab-status (둘 중 없으면 8로)
   `git -C /home/user/web1 fetch -q origin claude/sharp-brown-8lieqe && git -C /home/user/web1 worktree add /tmp/w <COMMIT>`
   cd /tmp/w/lab-v2 ; M="python tools/manager_run.py --repo $D"
2. `$M lock --run $RUN` → acquired=false면 8로.
3. `$M publish-state --dest $P --push`   (지피 점검 장면. 실패해도 계속)
4. `$M plan-observe` → schedule_expired=true면 '게이트 기간 종료'를 기록하고 7로.
5. Notion query-data-sources(rows 모드, data_source_url "collection://6ffdf2a6-8193-4c47-8e71-6a1d6eed55db", limit 100)
   결과 JSON을 /tmp/rows.json에 저장 → `$M inbox --rows /tmp/rows.json`. 실패하면 기록하고 계속.
6. `$M run-openai --run $RUN --max-jobs 1 --publish-dir $P --push`  (단계마다 공개 상태 갱신, 완료 시 비공개 보고서 생성)
7. `$M report` → `$M summary --run $RUN` → `$M unlock --run $RUN` → `$M publish-state --dest $P --push`
8. 결과 요약(잠금, 지시 처리, openai_calls, 비용, published, 오류)을 $D/ops/runs/$RUN.json 으로 저장하고
   `cd $D && git add -A && (git commit -qm "run: $RUN" || true) && git push origin HEAD:main`
   (거절 시 git pull --rebase 후 1회 재시도, 충돌이면 그대로 종료). 같은 요약을 최종 답변에도 쓰세요.
```
