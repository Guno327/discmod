import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discmod.git_ops import (
    checkout_branch,
    commit_and_push,
    create_pr_branch,
    get_last_commit,
    is_git_repo,
    pull_ff,
)

PACK = Path("/fake/pack")


def _cp(stdout="", stderr="", returncode=0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def test_is_git_repo_true():
    with patch("subprocess.run", return_value=_cp(returncode=0)):
        assert is_git_repo(PACK) is True


def test_is_git_repo_false():
    with patch("subprocess.run", return_value=_cp(returncode=1)):
        assert is_git_repo(PACK) is False


def test_commit_and_push_success():
    sha = "abc123def456"
    # Calls in order: git add, git commit, git rev-parse HEAD, git push
    with patch("subprocess.run", side_effect=[_cp(), _cp(), _cp(stdout=sha + "\n"), _cp()]):
        result = commit_and_push(PACK, "Add sodium 1.0", "body", "bot", "bot@x", "origin", "dev")
    assert result == sha


def test_commit_and_push_push_fails():
    sha = "abc123"
    error = subprocess.CalledProcessError(1, "git push", stderr="push failed")
    with patch("subprocess.run", side_effect=[_cp(), _cp(), _cp(stdout=sha + "\n"), error]):
        with pytest.raises(subprocess.CalledProcessError):
            commit_and_push(PACK, "msg", "body", "bot", "bot@x", "origin", "dev")


def test_pull_ff_success():
    with patch("subprocess.run", return_value=_cp()):
        pull_ff(PACK, "origin", "dev")  # must not raise


def test_pull_ff_non_fast_forward_warns(caplog):
    error = subprocess.CalledProcessError(1, "git pull", stderr="not a fast-forward")
    with patch("subprocess.run", side_effect=error):
        with caplog.at_level("WARNING"):
            pull_ff(PACK, "origin", "dev")  # must not raise
    assert "ff-only" in caplog.text


def test_get_last_commit_success():
    output = "deadbeef\nBot Author\nAdd sodium 1.0\n"
    with patch("subprocess.run", return_value=_cp(stdout=output)):
        result = get_last_commit(PACK)
    assert result["sha"] == "deadbeef"
    assert result["author"] == "Bot Author"
    assert result["subject"] == "Add sodium 1.0"


def test_get_last_commit_failure_returns_none():
    error = subprocess.CalledProcessError(1, "git log")
    with patch("subprocess.run", side_effect=error):
        assert get_last_commit(PACK) is None


def test_get_last_commit_short_output_returns_none():
    with patch("subprocess.run", return_value=_cp(stdout="only-one-line\n")):
        assert get_last_commit(PACK) is None


def test_create_pr_branch():
    with patch("subprocess.run", return_value=_cp()) as mock:
        create_pr_branch(PACK, "pr/sodium", "bot", "bot@x", "origin")
    assert mock.call_count == 2


def test_checkout_branch():
    with patch("subprocess.run", return_value=_cp()) as mock:
        checkout_branch(PACK, "main")
    assert mock.call_count == 1
