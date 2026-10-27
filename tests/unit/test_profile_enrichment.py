from dataclasses import replace

from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.infrastructure.browser.linkedin import (
    _explicit_role_company,
    enrich_visible_profile_text,
    extract_visible_profile,
)


def subject():
    return LeadCandidate(
        profile_url="https://www.linkedin.com/in/example/",
        full_name="Example Person",
        headline="CEO & co-founder of Example Labs",
        location="London",
    )


def test_sections_exclude_featured_activity_suggestions_and_footer():
    text = """Example Person
CEO & co-founder of Example Labs
London
16 connections
100 followers
People who follow Example also follow
Other Person
Independent trader at Other Company
50,000 followers
500-1000 employees
About
I build regulatory reporting software for banks.
Featured
Post
I used to work in proprietary trading.
Experience
CEO
Example Labs
Education
Example University
Skills
Software development
More profiles for you
Another Person
About
Accessibility
Talent Solutions
"""
    result = enrich_visible_profile_text(text, subject())
    assert result.about == "I build regulatory reporting software for banks."
    assert result.experience == ("CEO", "Example Labs")
    assert result.education == ("Example University",)
    assert result.skills == ("Software development",)
    assert result.company == "Example Labs" and result.current_title == "CEO & co-founder"
    assert result.followers == "100" and result.connections == "16"
    assert result.self_employed is None
    assert result.company_size_max is None


def test_company_is_not_inferred_from_recommendations_or_education():
    result = enrich_visible_profile_text(
        "Example Person\nStudent\nLondon\nContact info\nExample University\nAbout\nAccessibility\n",
        replace(subject(), headline="Civil Engineering Student"),
    )
    assert result.company == result.current_title == result.about == ""
    assert _explicit_role_company("Head of Investments") == ("", "")
    assert _explicit_role_company("Engineer@example.com") == ("", "")
    assert _explicit_role_company("Head of Investments at Example Fund") == (
        "Head of Investments",
        "Example Fund",
    )


def test_explicit_search_company_is_preserved():
    result = enrich_visible_profile_text(
        "Example Person\nAbout\nText\nFeatured\nPost",
        replace(subject(), company="Verified Search Company", current_title="Founder"),
    )
    assert result.company == "Verified Search Company"
    assert result.current_title == "Founder"


def test_profile_loading_wait_is_bounded_and_reads_late_visible_sections():
    class Main:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            pass

        def inner_text(self, **kwargs):
            return (
                "Example Person\nCEO & co-founder of Suade\nLondon\nAbout\nAccessibility"
                if page.waits < 2
                else "Example Person\nCEO & co-founder of Suade\nLondon\nAbout\n"
                "Visible subject description\nFeatured\nOther posts"
            )

    class Page:
        url = "https://www.linkedin.com/in/example/"
        waits = 0
        scrolls = 0

        @property
        def mouse(self):
            return self

        def locator(self, selector):
            return Main()

        def wait_for_timeout(self, delay):
            self.waits += 1

        def wheel(self, x, y):
            self.scrolls += 1

        def is_closed(self):
            return False

        def title(self):
            return "Example Person | LinkedIn"

        def content(self):
            return "Profile"

    page = Page()
    result = extract_visible_profile(page, subject())
    assert result.about == "Visible subject description"
    assert page.waits == 4
    assert page.scrolls == 2
