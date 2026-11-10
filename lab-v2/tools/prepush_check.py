"""lab-v2 상시 푸시 승인 범위 검사.

원격 브랜치 대비 변경이 lab-v2/ 안에만 있고, 추가된 줄에 비공개 정보 의심 패턴이
없을 때만 0을 반환한다. tests/ 의 의도적 탐지 예시는 경고만 하고 차단하지 않는다
(시험 고정값은 가짜 값만 쓰는 것이 규칙).

사용: python3 lab-v2/tools/prepush_check.py [기준 브랜치]
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.privacy import scan  # noqa: E402


def added_lines(base: str, path: str) -> str:
    diff = subprocess.run(["git", "diff", f"{base}..HEAD", "--", path],
                          capture_output=True, text=True, check=True).stdout
    return "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/claude/sharp-brown-8lieqe"
    files = subprocess.run(["git", "diff", "--name-only", f"{base}..HEAD"],
                           capture_output=True, text=True, check=True).stdout.split()
    outside = [f for f in files if not f.startswith("lab-v2/")]
    if outside:
        print("차단: lab-v2/ 밖 변경 — 사용자 확인 필요:", outside)
        return 2
    non_test = [f for f in files if not f.startswith("lab-v2/tests/")]
    hits = scan("\n".join(added_lines(base, f) for f in non_test)) if non_test else []
    if hits:
        print("차단: 비공개 정보 의심 패턴", sorted({h[0] for h in hits}), "— 사용자 확인 필요")
        return 3
    test_hits = scan(added_lines(base, "lab-v2/tests/"))
    if test_hits:
        print("경고: 시험 고정값에 탐지 패턴", sorted({h[0] for h in test_hits}), "— 가짜 값인지 확인")
    print(f"통과: {len(files)}개 파일, 모두 lab-v2/ 안, 의심 패턴 없음(정규식 검사이므로 누락 가능)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
