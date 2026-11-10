"""Git 원장 시험 — 로컬 임시 bare 저장소 2개 복제본으로 동시 쓰기를 실제 git으로 재현한다.
(원격 GitHub가 아니라 로컬 git이므로 '모의 환경의 실제 git 동작' 시험이다.)"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.ledger import ConflictError, TerminalStateError  # noqa: E402
from kernel.model import Stage, Status, Task  # noqa: E402
from kernel.repo_ledger import RepoLedger  # noqa: E402


def git(*a, cwd=None):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def two_clones(tmp_path):
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    git("clone", "-q", str(bare), str(seed))
    (seed / "README.md").write_text("x")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=seed)
    git("add", ".", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "readme", cwd=seed)
    git("push", "-q", "origin", "HEAD:main", cwd=seed)
    a, b = tmp_path / "a", tmp_path / "b"
    git("clone", "-q", str(bare), str(a))
    git("clone", "-q", str(bare), str(b))
    return RepoLedger(str(a)), RepoLedger(str(b))


def test_add_update_and_reload(two_clones):
    A, B = two_clones
    A.add(Task(id="P:collect:v0", project="P", stage=Stage.COLLECT))
    t = A.update("P:collect:v0", 0, status=Status.RUNNING, lease_owner="s1")
    B.sync()
    assert B.get("P:collect:v0").status == Status.RUNNING and t.rev == 1


def test_different_files_concurrently_both_land(two_clones):
    A, B = two_clones
    A.add(Task(id="P:collect:v0", project="P", stage=Stage.COLLECT))
    B.add(Task(id="Q:collect:v0", project="Q", stage=Stage.COLLECT))   # push 거절 → rebase → 성공
    A.sync()
    assert {t.id for t in A.all()} == {"P:collect:v0", "Q:collect:v0"}


def test_same_task_concurrent_claim_one_loses(two_clones):
    A, B = two_clones
    A.add(Task(id="P:collect:v0", project="P", stage=Stage.COLLECT))
    B.sync()
    A.update("P:collect:v0", 0, status=Status.RUNNING, lease_owner="runA")
    with pytest.raises(ConflictError):          # B는 오래된 rev 0을 보고 같은 과제를 잡으려 함
        B.update("P:collect:v0", 0, status=Status.RUNNING, lease_owner="runB")
    B.sync()
    assert B.get("P:collect:v0").lease_owner == "runA"


def test_terminal_and_event_dedupe(two_clones):
    A, _ = two_clones
    A.add(Task(id="P:collect:v0", project="P", stage=Stage.COLLECT, status=Status.DONE))
    with pytest.raises(TerminalStateError):
        A.update("P:collect:v0", 0, status=Status.RUNNING)
    assert A.record("e1", "2026-11-10", type="x") is True
    assert A.record("e1", "2026-11-10", type="x") is False
