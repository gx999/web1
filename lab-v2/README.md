# 정대용 디지털 연구소 v2 (격리 개발 디렉터리)

기존 홈페이지 파일·기존 ChatGPT 연구소와 분리된 새 운영체계의 설계·참조 구현이다.
개인정보·원고·인증정보는 이 디렉터리에 두지 않는다(저장소가 공개이기 때문).

- `docs/01-first-report.md` 첫 보고: 실행 기반 검증 결과, 비교, 구성도, 승인 필요 지점
- `docs/02-gippy-interface.md` 지피(ChatGPT) 연결 인터페이스 설계안
- `kernel/` 운영 관리자 참조 구현(원장 CAS·임대·재시도·한도 대기·단계 연결·공개 투영)
- `tests/` 모의시험 — 실제 운영 기록이 아님

```bash
cd lab-v2 && python3 -m pytest -q tests
```
