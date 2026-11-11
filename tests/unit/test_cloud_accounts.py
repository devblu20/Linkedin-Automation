"""Account linking must be one-use, private and recoverable by stable identity."""

import logging

import pytest

from linkedin_automation.infrastructure.cloud_accounts import CloudAccounts
from linkedin_automation.infrastructure.cloud_logging import CloudAccessFilter


def account(identifier="one", member="member-one"):
    return {"id": identifier, "type": "LINKEDIN", "connection_params": {"im": {"id": member}}}


def test_repeated_identity_recovers_same_workspace_and_tokens_are_hashed(tmp_path):
    store = CloudAccounts(tmp_path)
    user = store.identity("subject", "Name")
    assert store.identity("subject", "New name")["id"] == user["id"]
    token = store.session(user["id"])
    assert token.encode() not in store.path.read_bytes()
    assert store.user(token)["name"] == "New name"
    store.revoke(token)
    assert store.user(token) is None
    with pytest.raises(ValueError):
        store.workspace("../../outside")


def test_connection_ownership_and_reconnect_rules(tmp_path):
    store = CloudAccounts(tmp_path)
    first, second = store.identity("first", "First"), store.identity("second", "Second")
    state = store.flow("connection", first["id"])
    assert store.connection(state, account())["account_id"] == "one"
    assert store.connection(state, account())["account_id"] == "one"  # callback retry
    with pytest.raises(ValueError, match="another private workspace"):
        store.connection(store.flow("connection", second["id"]), account())
    with pytest.raises(ValueError, match="original LinkedIn"):
        store.connection(store.flow("connection", first["id"]), account("new", "different"))
    store.connection(store.flow("connection", first["id"]), account("new"))
    assert store.account_for(first["id"]) == "new"
    with pytest.raises(ValueError):
        store.account_for(second["id"])


def test_expired_and_wrong_kind_flows_fail_and_open_flows_are_bounded(tmp_path):
    store = CloudAccounts(tmp_path)
    user = store.identity("first", "First")
    state = store.flow("identity")
    with pytest.raises(ValueError):
        store.connection(state, account())
    with store.db() as db:
        db.execute("UPDATE cloud_flows SET expires=0")
    with pytest.raises(ValueError):
        store.consume(state, "identity")
    for _ in range(10):
        store.flow("connection", user["id"])
    with pytest.raises(ValueError, match="Too many"):
        store.flow("connection", user["id"])


def test_access_logs_remove_callback_secret_and_oauth_code():
    for path in ("notify?secret=private", "identity?code=private&state=private"):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            ("client", "POST", "/linkedin-automation/api/cloud/" + path, "1.1", 200),
            None,
        )
        assert CloudAccessFilter().filter(record)
        assert "private" not in record.getMessage()
