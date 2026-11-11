"""Explicit account replacement must not bypass original reconnect/ownership protections."""

import pytest

from linkedin_automation.infrastructure.cloud_accounts import CloudAccounts

TARGET = "https://www.linkedin.com/in/current-profile/"


def account(identifier, member, slug="current-profile"):
    return {
        "id": identifier,
        "type": "LINKEDIN",
        "connection_params": {"im": {"id": member, "publicIdentifier": slug}},
    }


@pytest.fixture
def linked(tmp_path):
    store = CloudAccounts(tmp_path)
    owner = store.identity("website:owner", "Owner")
    old = account("old-account", "old-member", "old-profile")
    store.connection(store.flow("connection", owner["id"]), old)
    return store, owner, old


def test_new_login_is_one_use_exact_profile_and_preserves_workspace_session(linked):
    store, owner, old = linked
    token = store.session(owner["id"])
    workspace = store.workspace(owner["id"])
    stale = store.flow("connection", owner["id"])
    state = store.replacement_flow(owner["id"], old["id"], TARGET)
    assert store.account_for(owner["id"]) == old["id"]
    assert store.replacement_owner(state) == owner["id"]
    new = account("new-account", "new-member")
    result = store.connection(state, new)
    assert result["id"] == owner["id"] and result["account_id"] == "new-account"
    assert store.user(token)["id"] == owner["id"]
    assert store.workspace(owner["id"]) == workspace
    assert store.connection(state, new) == result
    assert store.replacement_owner(state) is None
    with pytest.raises(ValueError, match="already used"):
        store.connection(stale, old)


@pytest.mark.parametrize("slug", ["other-profile", "", "../current-profile"])
def test_wrong_or_missing_profile_proof_never_rebinds(linked, slug):
    store, owner, old = linked
    state = store.replacement_flow(owner["id"], old["id"], TARGET)
    with pytest.raises(ValueError):
        store.connection(state, account("new-account", "new-member", slug))
    assert store.account_for(owner["id"]) == old["id"]


def test_account_owned_by_other_workspace_is_not_appropriated(linked):
    store, owner, old = linked
    other = store.identity("website:other", "Other")
    foreign = account("foreign-account", "foreign-member")
    store.connection(store.flow("connection", other["id"]), foreign)
    state = store.replacement_flow(owner["id"], old["id"], TARGET)
    with pytest.raises(ValueError, match="another private workspace"):
        store.connection(state, foreign)
    assert store.account_for(owner["id"]) == old["id"]
    assert store.account_for(other["id"]) == foreign["id"]


def test_normal_reconnect_still_refuses_switch_and_replacement_detects_race(linked):
    store, owner, old = linked
    state = store.flow("connection", owner["id"])
    with pytest.raises(ValueError, match="original LinkedIn"):
        store.connection(state, account("new-account", "new-member"))
    replacement = store.replacement_flow(owner["id"], old["id"], TARGET)
    store.connection(
        store.flow("connection", owner["id"]), account("rotated-account", "old-member")
    )
    with pytest.raises(ValueError, match="changed during"):
        store.connection(replacement, account("new-account", "new-member"))
    assert store.account_for(owner["id"]) == "rotated-account"


def test_expired_replacement_and_non_linkedin_target_are_rejected(linked):
    store, owner, old = linked
    with pytest.raises(ValueError):
        store.replacement_flow(owner["id"], old["id"], "https://example.com/in/current-profile/")
    state = store.replacement_flow(owner["id"], old["id"], TARGET)
    with store.db() as db:
        db.execute("UPDATE cloud_flows SET expires=0")
    with pytest.raises(ValueError, match="expired"):
        store.connection(state, account("new-account", "new-member"))
    assert store.account_for(owner["id"]) == old["id"]
