"""Public detail extraction must retain evidence, not fill blanks from campaign queries."""

from linkedin_automation.infrastructure.public_profile_details import public_profile_details


def test_explicit_labels_and_role_at_company_are_extracted_with_evidence():
    data = public_profile_details(
        "Alex Person - Founder at Acme",
        "Location: London, England, United Kingdom · Industry: Recruitment · "
        "Education: Example University · Skills: CRM, Python · "
        "Experience: Project Manager at Earlier Ltd · About: Builds internal tools · "
        "500+ connections and 2K followers",
        "Alex Person",
    )
    assert data["current_role"] == "Founder" and data["company"] == "Acme"
    assert data["location"] == "London, England, United Kingdom"
    assert data["skills"] == ["CRM", "Python"]
    assert data["education"] == ["Example University"]
    assert data["experience"] == ["Project Manager at Earlier Ltd"]
    assert data["industry"] == "Recruitment" and data["about"] == "Builds internal tools"
    assert data["connections"] == "500+" and data["followers"] == "2K"
    assert data["field_evidence"]["location"]["status"] == "public_index_unverified"


def test_incidental_locations_or_prior_jobs_never_become_current_facts():
    data = public_profile_details(
        "Alex Person - Former Founder at OldCo",
        "Works with clients in London. Experience: Analyst at Previous Ltd · "
        "Education: London School",
        "Alex Person",
    )
    assert data["location"] == data["current_role"] == data["company"] == ""
    assert data["education"] == ["London School"]
    assert "location" in data["missing_fields"]


def test_truncated_labeled_values_are_not_treated_as_full_fields():
    data = public_profile_details(
        "Alex Person", "Location: London... · Company: Acme…", "Alex Person"
    )
    assert data["location"] == data["company"] == ""


def test_based_in_phrase_and_current_role_labels_are_public_evidence():
    data = public_profile_details(
        "Alex Person", "Based in Manchester. Current role: Director · Company: Example Ltd"
    )
    assert data["location"] == "Manchester" and data["current_role"] == "Director"
    assert data["company"] == "Example Ltd"


def test_clients_followers_are_not_the_profiles_own_followers():
    data = public_profile_details("Alex Person", "Helps clients gain 5000 followers")
    assert data["followers"] == ""


def test_linkedin_footer_and_reposted_content_never_extend_location():
    data = public_profile_details(
        "Direct Trader",
        "Experience: Example Ltd · Location: Greater London. "
        "View Direct Trader\u2019s profile on LinkedIn, "
        "a professional community of 1 billion members. "
        "Company: Reposted Business · Current role: Founder",
    )
    assert data["location"] == "Greater London"
    assert data["company"] == data["current_role"] == ""


def test_own_connections_with_linkedin_suffix_are_readable():
    data = public_profile_details(
        "Alex Person", "500+ connections on LinkedIn. View Alex Person\u2019s profile on LinkedIn"
    )
    assert data["connections"] == "500+"


def test_conflicting_role_excerpt_cannot_fill_employer():
    data = public_profile_details(
        "Alex Person",
        "Current role: Founder\nCurrent role: Director · Current company: OtherCo\nSkills: CRM",
    )
    assert data["current_role"] == "Founder" and data["company"] == ""
    assert data["skills"] == ["CRM"]
