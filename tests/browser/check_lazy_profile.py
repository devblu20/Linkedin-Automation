"""Chrome regression for profiles loaded inside a nested scroll container."""

# ruff: noqa: E501 - embedded offline HTML fixture.

from playwright.sync_api import sync_playwright

from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.infrastructure.browser.linkedin import extract_visible_profile

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.set_content("""<style>body{margin:0}main{margin-left:260px;width:800px;height:600px;overflow:auto}</style>
    <main><h2>Example Person</h2><p>Independent Trader</p><p>London</p><h2>About</h2><p>Subject biography</p><div style="height:2000px"></div></main>
    <script>const pane=document.querySelector('main');pane.addEventListener('scroll',()=>{if(pane.scrollTop>500&&!document.getElementById('experience')){pane.insertAdjacentHTML('beforeend','<section id="experience"><h2>Experience</h2><p>Independent Trader</p><p>Example Trading · Self-employed</p><p>Jan 2024 - Present · 2 years</p></section><section><h2>Education</h2><p>Example College</p></section><section><h2>Skills</h2><p>Python</p></section>')}});</script>""")
    result = extract_visible_profile(
        page,
        LeadCandidate(
            profile_url="https://www.linkedin.com/in/example/", full_name="Example Person"
        ),
    )
    assert result.company == "Example Trading"
    assert result.current_title == "Independent Trader"
    assert result.self_employed is True
    assert result.education == ("Example College",)
    assert result.about == "Subject biography"
    browser.close()
    print("PASS: automatic Experience/company/role extraction in a lazy nested profile pane")
