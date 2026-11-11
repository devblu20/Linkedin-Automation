"""Provider mapping and hard visit budgets with no external network."""

import re
from pathlib import Path

import pytest

from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.unipile_client import UnipileClient, UnipileSettings
from linkedin_automation.infrastructure.unipile_collection import (
    UnipileCollector,
    profile_candidate,
)


def test_v2_profile_mapping_uses_only_returned_fields():
    candidate = profile_candidate(
        {
            "display_name": "Example User",
            "profile_url": "https://www.linkedin.com/in/example/",
            "description": "Fund manager",
            "bio": "Visible summary",
            "specifics": {
                "linkedin": {
                    "experience": [
                        {
                            "title": "Fund Manager",
                            "company": {"name": "Example Fund"},
                            "ended_on": None,
                        }
                    ],
                    "skills": [{"text": "Trading"}],
                }
            },
            "location": "London",
        },
        "api",
    )
    assert candidate.full_name == "Example User" and candidate.company == "Example Fund"
    assert candidate.current_title == "Fund Manager" and candidate.skills == ("Trading",)
    assert candidate.industry == "" and candidate.company_size_min is None
    assert (
        profile_candidate(
            {"name": "LinkedIn Member ", "profile_url": "https://www.linkedin.com/in/hidden/"},
            "api",
        )
        is None
    )
    assert (
        profile_candidate(
            {"name": "Example", "profile_url": "https://other.test/in/example/"}, "api"
        )
        is None
    )


def test_v1_search_protocol_and_v2_hosted_auth_are_distinct(monkeypatch):
    config = UnipileSettings(
        "https://www.bluqq.com", "https://api56.unipile.com:18643", "not-a-real-key", 1
    )
    client = UnipileClient(config)
    calls = []
    monkeypatch.setattr(
        client, "request", lambda path, payload=None: calls.append((path, payload)) or {"items": []}
    )
    client.search(
        "private-account", "https://www.linkedin.com/search/results/people/?keywords=trading"
    )
    assert calls[0][0] == "/api/v1/linkedin/search?account_id=private-account"
    config = UnipileSettings(
        "https://www.bluqq.com", "https://api.unipile.com", "not-a-real-key", 2
    )
    client = UnipileClient(config)
    calls = []
    monkeypatch.setattr(
        client,
        "request",
        lambda path, payload=None: (
            calls.append((path, payload)) or {"link": "https://auth.unipile.com/private-link"}
        ),
    )
    client.hosted_link("x" * 64, scope_id="scope_one")
    body = calls[0][1]
    assert body["providers"] == ["linkedin"] and body["account_scope_id"] == "scope_one"
    assert "notify_url" not in body and "api_url" not in body
    assert "flow=" in body["redirect_uri"]


def test_v1_hosted_auth_accepts_provider_utc_millisecond_schema(monkeypatch):
    from linkedin_automation.infrastructure.cloud_connector import CloudConnector

    calls = []
    monkeypatch.setattr(
        CloudConnector,
        "request",
        lambda self, path, payload=None: (
            calls.append((path, payload)) or {"url": "https://account.unipile.com/test"}
        ),
    )
    client = UnipileClient(
        UnipileSettings(
            "https://www.bluqq.com",
            "https://api70.unipile.com:20051",
            "test-key",
            1,
            "s" * 40,
        )
    )
    client.hosted_link("x" * 64)
    body = calls[0][1]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", body["expiresOn"])
    assert body["providers"] == ["LINKEDIN"]


def test_cloud_visits_are_capped_and_duplicates_are_not_revisited():
    class Provider:
        def __init__(self):
            self.visits = []
            self.searches = 0

        def search(self, *args, **kwargs):
            self.searches += 1
            return {
                "data": [
                    {
                        "display_name": "Named " + str(x),
                        "profile_url": "https://www.linkedin.com/in/p" + str(x) + "/",
                        "id": str(x),
                        "location": "London",
                    }
                    for x in [1, 1, 2, 3]
                ]
            }

        def profile(self, account, identifier):
            self.visits.append(identifier)
            return {
                "display_name": "Named " + identifier,
                "profile_url": "https://www.linkedin.com/in/p" + identifier + "/",
                "location": "London",
                "specifics": None,
            }

    provider = Provider()
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-small-funds-trading-teams.yaml")
    )
    definition = definition.model_copy(
        update={"limits": definition.limits.model_copy(update={"max_results": 2, "max_pages": 3})}
    )
    result = UnipileCollector(provider, "only-my-account").collect(definition)
    assert provider.visits == ["1", "2"] and provider.searches == 1
    assert len(result.candidates) == 2


def test_v1_message_transport_uses_multipart_with_exact_recipient_and_text(monkeypatch):
    import json

    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return json.dumps(
                {"object": "ChatStarted", "chat_id": "chat", "message_id": "message"}
            ).encode()

    class Opener:
        def open(self, request, **kwargs):
            calls.append(request)
            return Response()

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.unipile_client.build_opener", lambda *args: Opener()
    )
    client = UnipileClient(
        UnipileSettings(
            "https://www.bluqq.com", "https://api70.unipile.com:20051", "test-secret", 1
        )
    )
    client.send_message("sender-account", "ACoRecipient123", "Thanks, Alex! £100")
    request = calls[0]
    assert request.full_url.endswith("/api/v1/chats")
    assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
    body = request.data.decode("utf-8")
    assert 'name="attendees_ids"\r\n\r\nACoRecipient123\r\n' in body
    assert 'name="account_id"\r\n\r\nsender-account\r\n' in body
    assert 'name="text"\r\n\r\nThanks, Alex! £100\r\n' in body
    assert "test-secret" not in body


def test_bad_optional_profile_sections_do_not_abort_mapping():
    candidate = profile_candidate(
        {
            "name": "Visible Trader",
            "public_identifier": "visible-trader",
            "specifics": ["unsupported"],
            "education": {"unknown": True},
            "skills": 12,
        },
        "api",
    )
    assert candidate.full_name == "Visible Trader"
    assert candidate.skills == () and candidate.education == ()


@pytest.mark.parametrize("status", [404, 422])
def test_one_unreadable_detail_page_preserves_named_cards_and_other_profiles(status):
    from linkedin_automation.infrastructure.unipile_client import UnipileError

    class Provider:
        def search(self, *args, **kwargs):
            return {
                "items": [
                    {
                        "id": str(n),
                        "name": "Trader " + str(n),
                        "public_identifier": "trader-" + str(n),
                        "headline": "Independent trader",
                        "location": "London",
                    }
                    for n in (1, 2)
                ]
            }

        def profile(self, account, identifier):
            if identifier == "1":
                raise UnipileError("Profile unavailable", status=status)
            return {"name": "Trader 2", "public_identifier": "trader-2", "summary": "Visible bio"}

    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    definition = definition.model_copy(
        update={"limits": definition.limits.model_copy(update={"max_results": 2, "max_pages": 1})}
    )
    result = UnipileCollector(Provider(), "my-account").collect(definition)
    assert [c.full_name for c in result.candidates] == ["Trader 1", "Trader 2"]
    assert result.candidates[0].headline == "Independent trader"
    assert result.candidates[0].about == ""  # Unreturned details are never invented.
    assert result.candidates[1].about == "Visible bio"
    assert result.candidates[1].location == "London"
    assert result.candidates[1].headline == "Independent trader"


def test_null_profile_body_is_not_treated_as_fabricated_profile():
    assert profile_candidate(None, "api") is None


def test_known_profile_lookup_accepts_wrapped_provider_response_and_checks_identity():
    from linkedin_automation.application.exceptions import CollectionError
    from linkedin_automation.infrastructure.unipile_collection import KnownTestProfileCollector

    class Provider:
        slug = "visible-trader"

        def profile(self, *args):
            return {"data": {"name": "Visible Trader", "public_identifier": self.slug}}

    provider = Provider()
    collector = KnownTestProfileCollector(
        provider, "my-account", "https://www.linkedin.com/in/visible-trader/"
    )
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    assert collector.collect(definition).candidates[0].full_name == "Visible Trader"
    provider.slug = "different-trader"
    with pytest.raises(CollectionError, match="exact approved test profile"):
        collector.collect(definition)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_detail_auth_quota_and_server_errors_still_stop_without_retry(status):
    from linkedin_automation.application.exceptions import CollectionError
    from linkedin_automation.infrastructure.unipile_client import UnipileError

    class Provider:
        calls = 0

        def search(self, *args, **kwargs):
            return {"items": [{"name": "Trader", "public_identifier": "trader"}]}

        def profile(self, *args):
            self.calls += 1
            raise UnipileError("Provider stopped", status=status)

    provider = Provider()
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    with pytest.raises(CollectionError, match="Provider stopped"):
        UnipileCollector(provider, "my-account").collect(definition)
    assert provider.calls == 1


@pytest.mark.parametrize(
    "sources",
    [[], None, [{"status": "CREDENTIALS"}], [{"status": "CONNECTING"}], [{"status": "OK"}, {}]],
)
def test_v1_does_not_mark_missing_or_unready_provider_status_as_connected(monkeypatch, sources):
    from linkedin_automation.infrastructure.unipile_client import UnipileAccountUnavailable

    client = UnipileClient(
        UnipileSettings("https://www.bluqq.com", "https://api70.unipile.com:20051", "test-key", 1)
    )
    monkeypatch.setattr(
        client, "request", lambda *args: {"id": "account", "type": "LINKEDIN", "sources": sources}
    )
    with pytest.raises(UnipileAccountUnavailable):
        client.account("account")
