from pathlib import Path

from linkedin_automation.application.search_plan import build_people_search_steps
from linkedin_automation.domain.search_definition import IncludeExcludeCriteria
from linkedin_automation.infrastructure.browser.linkedin import build_people_search_url
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def test_required_role_drives_discovery_without_relaxing_profile_filters():
    base = YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))
    criteria = base.search.model_copy(
        update={
            "titles": IncludeExcludeCriteria(include=["Founder", "Owner"]),
            "keywords": IncludeExcludeCriteria(include=["IT consulting", "IT consultancy"]),
            "required_dimensions": ["titles", "keywords", "locations"],
        }
    )
    definition = base.model_copy(
        update={"search": criteria, "limits": base.limits.model_copy(update={"max_pages": 3})}
    )
    steps = build_people_search_steps(definition)
    assert steps == [
        ('"Founder" AND "IT consulting"', 1),
        ('"Owner" AND "IT consultancy"', 1),
        ('"Founder" AND "IT consulting"', 2),
    ]
    assert definition.search.required_dimensions == ["titles", "keywords", "locations"]
    url = build_people_search_url(definition, steps[0][1], search_term=steps[0][0])
    assert "%22Founder%22+AND+%22IT+consulting%22" in url
    assert "geoUrn=" in url


def test_optional_role_campaign_keeps_existing_subject_query_plan():
    definition = YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))
    assert build_people_search_steps(definition)[0] == ("recruitment agency", 1)
