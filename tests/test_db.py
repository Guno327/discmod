import pytest

from discmod.db import (
    count_approvals,
    fail_merging_proposals,
    get_pending_proposals,
    get_proposal,
    insert_approval,
    insert_proposal,
    open_db,
    transition_to_merging,
    update_proposal_status,
    update_thread_id,
)


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "test.db")
    yield c
    c.close()


def _insert(conn, message_id=1, slug="sodium"):
    insert_proposal(
        conn,
        message_id=message_id,
        channel_id=100,
        mod_url="https://modrinth.com/mod/sodium",
        slug=slug,
        project_id="AANobbMI",
        proposer_id=42,
        proposer_name="Alice",
    )


def test_open_db_creates_tables(tmp_path):
    c = open_db(tmp_path / "test.db")
    tables = {
        row[0]
        for row in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "proposals" in tables
    assert "approvals" in tables
    c.close()


def test_insert_and_get_proposal(conn):
    insert_proposal(
        conn,
        message_id=1,
        channel_id=100,
        mod_url="url",
        slug="sodium",
        project_id="AANobbMI",
        proposer_id=42,
        proposer_name="Alice",
        ai_summary="fast renderer",
    )
    row = get_proposal(conn, 1)
    assert row["slug"] == "sodium"
    assert row["status"] == "pending"
    assert row["ai_summary"] == "fast renderer"
    assert row["proposer_name"] == "Alice"


def test_get_proposal_not_found(conn):
    assert get_proposal(conn, 999) is None


def test_get_pending_proposals_filters_status(conn):
    _insert(conn, 1)
    _insert(conn, 2)
    update_proposal_status(conn, 2, "merged")
    pending = get_pending_proposals(conn)
    assert len(pending) == 1
    assert pending[0]["message_id"] == 1


def test_get_pending_proposals_empty(conn):
    assert get_pending_proposals(conn) == []


def test_update_proposal_status_sets_version(conn):
    _insert(conn, 1)
    rowcount = update_proposal_status(conn, 1, "merged", resolved_version="1.0.0")
    assert rowcount == 1
    row = get_proposal(conn, 1)
    assert row["status"] == "merged"
    assert row["resolved_version"] == "1.0.0"
    assert row["decided_at"] is not None


def test_update_proposal_status_sets_error(conn):
    _insert(conn, 1)
    update_proposal_status(conn, 1, "failed", error="oops")
    row = get_proposal(conn, 1)
    assert row["status"] == "failed"
    assert row["error"] == "oops"


def test_update_proposal_status_nonexistent(conn):
    rowcount = update_proposal_status(conn, 999, "merged")
    assert rowcount == 0


def test_transition_to_merging_success(conn):
    _insert(conn, 1)
    result = transition_to_merging(conn, 1)
    assert result is True
    assert get_proposal(conn, 1)["status"] == "merging"


def test_transition_to_merging_already_merging(conn):
    _insert(conn, 1)
    transition_to_merging(conn, 1)
    result = transition_to_merging(conn, 1)
    assert result is False


def test_update_thread_id(conn):
    _insert(conn, 1)
    update_thread_id(conn, 1, 9999)
    assert get_proposal(conn, 1)["thread_id"] == 9999


def test_insert_approval_and_count(conn):
    _insert(conn, 1)
    insert_approval(conn, 1, 10, "Bob")
    insert_approval(conn, 1, 11, "Carol")
    assert count_approvals(conn, 1, 42) == 2


def test_insert_approval_ignores_duplicate(conn):
    _insert(conn, 1)
    insert_approval(conn, 1, 10, "Bob")
    insert_approval(conn, 1, 10, "Bob")
    assert count_approvals(conn, 1, 42) == 1


def test_count_approvals_excludes_proposer(conn):
    _insert(conn, 1)
    insert_approval(conn, 1, 42, "Alice")  # proposer
    insert_approval(conn, 1, 10, "Bob")
    assert count_approvals(conn, 1, 42) == 1


def test_fail_merging_proposals(conn):
    _insert(conn, 1)
    _insert(conn, 2)
    transition_to_merging(conn, 1)
    transition_to_merging(conn, 2)
    ids = fail_merging_proposals(conn)
    assert set(ids) == {1, 2}
    assert get_proposal(conn, 1)["status"] == "failed"
    assert "restarted" in get_proposal(conn, 1)["error"]


def test_fail_merging_proposals_when_none(conn):
    ids = fail_merging_proposals(conn)
    assert ids == []


def test_fail_merging_proposals_skips_non_merging(conn):
    _insert(conn, 1)
    update_proposal_status(conn, 1, "merged")
    ids = fail_merging_proposals(conn)
    assert ids == []
