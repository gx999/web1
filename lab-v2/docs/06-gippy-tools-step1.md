# 지피 연결 시험 1단계 결과 분석 (사용자 제공 도구 목록, 2026-11-10)

출처: 사용자가 지피에게서 받아 전달한 도구 이름 목록(3부). **도구 이름이 보인다는 것과 호출이 성공한다는 것은 다르다** — 아래는 이름 기준 분류이며 2·3단계 실호출 전까지 '미확인'.
접두어가 `mcp__codex_apps__`라 ChatGPT(Codex 앱 연결) 환경의 목록으로 보인다. ChatGPT **예약 실행에서도 같은 도구가 제공되는지는 별도 확인 필요**.

## 연구소와 관련된 도구

| 묶음 | 도구(요약) | 의미 |
|---|---|---|
| 기존 연구소 MCP `app_8eccdb31ff17` | `lab_get_progress`, `lab_command`, `lab_claim_work`, `lab_submit_research`, `lab_report_progress`, `lab_begin_review`, `lab_save_first_view`, `lab_get_review_packet`, `lab_read_review_sources`, `lab_submit_claim_review`, `lab_abandon_work/review`, `lab_read_research`, `lab_get/save_daily_report`, `lab_get_public_projection`, `lab_initialize` | 기존 연구소 원장의 과제 조회·지시·인수·제출·독립검토(초안 전 '첫 의견' 저장 포함)가 지피에게 연결돼 있음. v2 설계와 기능이 거의 대응됨 |
| 공개 상태 `app_a35e0d02d88a` | `lab_publish_public_projection` | 홈페이지 픽셀 사무실이 읽는 공개 상태 서버에 게시 |
| 원고함 `app_b89c8efa16d4` | `list_project_context`, `read_manuscript`, `search_manuscript`, `save_review` | 기존 비공개 원고함 |
| ChatGPT 예약 | `automations_create/update/get/list/run_now/peek` | 지피가 예약 작업을 만들고 즉시 실행 가능 → 지피 쪽 정기 보고·조회 경로 |
| Notion | `notion_search`, `notion_fetch`, `notion_query_data_sources`, `notion_notion_create_pages`, `notion_notion_update_page` 등 | 읽기·쓰기 도구 이름 존재. 쓰기 시 승인 카드 여부 미확인 |
| GitHub | `github_fetch_file`, `github_create_file`, `github_update_file`, `github_create_commit`, `github_create_branch`, `github_create_pull_request`, `github_merge_pull_request` 등 | **쓰기 도구 존재.** Claude 연구원은 git 푸시가 승인 없이 동작함을 실제 확인 → 비공개 저장소를 공유 원장으로 쓰는 새 후보(D) |
| ChatGPT Sites | `sites_read_database_table_rows`, `sites_deploy_*`, `sites_update_environment_variables`, `sites_create_source_repository_write_credential` 등 | 기존 연구소 서버의 호스팅·DB를 지피가 관리 가능(운영 변경 권한 — 시험에서 사용 금지) |

## 위험 표시 (시험에서 호출하지 않을 도구)
- `openai_platform_create_encrypted_openai_api_key`, `sites_update_environment_variables`, `sites_create_source_repository_write_credential`, `sites_deploy_*`, `sites_update_site_access`: 인증정보·배포·공개 범위 변경 → 사용자 승인 대상.
- `gmail_send_email`, `slackbot_send_message`, `github_merge_pull_request`, `*_share_*`: 외부 전송·공유.
- `lab_command`, `lab_claim_work`, `lab_submit_*`: 기존 연구소 원장 **변경** — v2 시험 중에는 조회 도구만 사용.

## 저장 위치 후보 갱신
| 후보 | 지피 측 | Claude 측 | 비고 |
|---|---|---|---|
| A 기존 연구소 MCP | 도구 완비(이름 확인) | HTTP 호출에 소유자 범위 인증 필요(승인) | 기존 서비스 확장 |
| B Notion | 쓰기 도구 이름 있음, 승인 카드 미확인 | 무인 쓰기 승인 대기로 멈춤(실제) | |
| C 새 원장 서버 | 개발자 모드 연결 필요 | 토큰 주입 | 구축량 큼 |
| **D 비공개 GitHub 저장소** | `github_*` 읽기·쓰기 도구 이름 있음 | git 푸시 무인 동작(실제 확인) | 원고 보관과 겸용 가능. 사용자가 비공개 저장소 생성·양쪽 접근 허용 필요 |

## 2단계에 추가할 시험 (사용자 승인 후)
- (기존) Notion 「작업 대기열 (시험)」 조회 + `G-1` 행 추가.
- (기존 연구소, 조회만) `lab_get_progress` 호출 결과 확인.
- (D안 확인, 읽기만) `github_fetch_file`로 `gx999/web1` 브랜치 `claude/sharp-brown-8lieqe`의 `lab-v2/README.md` 읽기. 쓰기 시험은 비공개 저장소가 생긴 뒤에만.

## 2단계 결과 (사람 있는 상태) — 지피 보고 + Claude 쪽 대조 [실제]
| 시험 | 지피 보고 | Claude 쪽 대조 |
|---|---|---|
| Notion 읽기 | 추가 전 7행과 상태 | 일치. 지피는 `P1:review:v0`를 `checkpointed`로 읽음 — 원장은 04:38:48Z에 `done`으로 바뀌었고 `G-1` 생성(04:38:35Z)이 그보다 앞서므로 읽은 시점과 정합 |
| Notion 쓰기 | `G-1-지피등록-시험` 생성(단계 시험·queued·시험 체크) | 존재 확인, createdTime 04:38:35Z, 값 일치. 실행 대상에서 제외 |
| 승인 카드 | 도구 응답으로는 표시 여부 불명 | **사용자 확인: 카드 뜨지 않음** (2026-11-10) — 사람 있는 상태의 Notion 쓰기는 승인 단계 없이 수행됨 |
| 기존 연구소 `lab_get_progress` | 읽기 1회 성공, 동시실행 1, 오늘 연구 1/2·검토 2/2, 잔여 크레딧 미관측 | Claude 쪽에서 해당 서버 접근 불가(허용 목록 밖) → 대조 불가. 지피 보고로만 기록 |
| GitHub 읽기 | README 첫 줄 '정대용 디지털 연구소 v2 (격리 개발 디렉터리)' | 원격 브랜치 첫 줄 `# 정대용 디지털 연구소 v2 (격리 개발 디렉터리)`와 일치(제목 기호 제외) |

판단: 사람이 있는 상태에서 지피는 Notion 읽기·쓰기, 기존 연구소 조회, GitHub 읽기가 **가능**. 무인(예약) 실행 가능 여부는 3단계 전까지 미확인.
참고: 기존 연구소에 '최근 침해사고 대응 연구'가 blocked로 있음 — v2 P1과 주제 중복 여부를 사용자 판단 대상으로 둔다(기존 원장은 변경하지 않음).

## 3단계 결과 (예약 실행 의도) — 지피 보고 + Claude 쪽 대조 [실제, 일부 미확인]
| 항목 | 지피 보고 | Claude 쪽 대조 |
|---|---|---|
| `G-2-지피예약-시험` 생성 | 성공, 오류 없음 | 존재 확인, createdTime 2026-11-10 04:58:43Z, 값 일치 |
| 추가 전 집계 | 10행: done 6·blocked 2·running 1·queued 1 | done 6·blocked 2·queued 1 일치. **running 1은 실제 checkpointed 1**(`P1:review:v1`, 04:49:32Z 이후 checkpointed) — 지피가 상태를 묶어 셌거나 이전 값을 읽었을 가능성 |
| 예약 실행 여부 | 행 편집 시각이 예정보다 약 25초 빠름 | Notion 기록만으로는 예약 실행인지 대화 중 직접 실행인지 구분 불가 → **미확인** |
| 승인 카드 | 도구 응답으로 확인 불가 | 사용자 확인 필요 |

판단: 무인 예약 실행에서의 쓰기 가능성은 **아직 확정하지 않는다**. 지피의 예약 실행 기록(`automations_get`/`peek`의 예정 시각·실제 실행 시각·상태)을 받아 대조해야 한다.
