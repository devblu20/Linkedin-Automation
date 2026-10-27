from pathlib import Path

import pytest

from linkedin_automation.application.exceptions import LimitedVisibilityError
from linkedin_automation.infrastructure.browser.direct_profile import (
    DirectProfileCollector,
    parse_visible_profile_summary,
)
from linkedin_automation.infrastructure.browser.linkedin import _profile_section


def test_direct_profile_reads_visible_fields_without_inferring_from_url():
    candidate = parse_visible_profile_summary(
        "Example Person\n\nCivil Engineer\n\nNigeria\nContact info\nConnect",
        "https://www.linkedin.com/in/unrelated-slug/?tracking=1",
    )
    assert candidate.full_name == "Example Person"
    assert candidate.headline == "Civil Engineer"
    assert candidate.location == "Nigeria"
    assert candidate.profile_url == "https://www.linkedin.com/in/unrelated-slug/"
    assert candidate.company == ""


@pytest.mark.parametrize(
    "text", ["", "LinkedIn Member\nEngineer\nNigeria", "Name\nEngineer\nConnect"]
)
def test_rejects_unavailable_or_incomplete_profile(text):
    with pytest.raises(LimitedVisibilityError):
        parse_visible_profile_summary(text, "https://www.linkedin.com/in/example/")


def test_direct_profile_rejects_non_linkedin_url_before_launch():
    with pytest.raises(ValueError):
        DirectProfileCollector("https://example.com/in/example/", Path("unused"))


def test_empty_about_section_does_not_capture_footer():
    assert (
        _profile_section("About\nAccessibility\nTalent Solutions", "About", ("accessibility",))
        == ()
    )
