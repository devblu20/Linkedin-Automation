"""Persistent versioned campaign edits without changing bundled YAML templates."""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from linkedin_automation.application.campaigns import (
    CampaignPolicy,
    CampaignSpec,
    compile_campaign,
    definition_hash,
    spec_from_template,
)
from linkedin_automation.application.manual_templates import manual_definition
from linkedin_automation.application.search_plan import build_people_search_steps
from linkedin_automation.domain.search_definition import SearchDefinition


class CampaignConflict(ValueError):
    pass


class CampaignCatalog:
    def __init__(self, path: Path, templates: dict):
        self.path, self.bundled_templates = path, dict(templates)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY, spec TEXT NOT NULL, template_hash TEXT NOT NULL,
                revision INTEGER NOT NULL, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("""CREATE TABLE IF NOT EXISTS manual_campaign_templates (
                id TEXT PRIMARY KEY, definition TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    @property
    def templates(self):
        with self.connection() as db:
            rows = db.execute(
                "SELECT id,definition FROM manual_campaign_templates ORDER BY created,id"
            )
            custom = {
                row["id"]: SearchDefinition.model_validate_json(row["definition"]) for row in rows
            }
        return {**custom, **self.bundled_templates}

    def create_template(self, payload):
        base = self.templates.get(payload.spec.template_id)
        if base is None:
            raise ValueError("Select an existing template as the starting point")
        identifier = "custom-template-" + str(uuid4()) + ".yaml"
        definition = manual_definition(base, payload, identifier)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM manual_campaign_templates").fetchone()[0] >= 100:
                raise ValueError("The workspace supports up to 100 manual templates")
            db.execute(
                "INSERT INTO manual_campaign_templates(id,definition) VALUES (?,?)",
                (identifier, definition.model_dump_json()),
            )
        return identifier, definition

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def get(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (identifier,)).fetchone()
        if row:
            return {
                "id": identifier,
                "revision": row["revision"],
                "spec": CampaignSpec.model_validate_json(row["spec"]),
                "template_hash": row["template_hash"],
            }
        template = self.templates.get(identifier)
        if template is None:
            raise KeyError(identifier)
        return {
            "id": identifier,
            "revision": 0,
            "spec": spec_from_template(identifier, template),
            "template_hash": definition_hash(template),
        }

    def save(
        self,
        spec: CampaignSpec,
        *,
        identifier: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        template = self.templates.get(spec.template_id)
        if template is None:
            raise ValueError("Select a trusted campaign template")
        creating = identifier is None
        identifier = identifier or "campaign-" + str(uuid4())
        compile_campaign(template, spec, identifier)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (identifier,)).fetchone()
            if not creating:
                if row is None and identifier not in self.templates:
                    raise KeyError(identifier)
                revision = row["revision"] if row else 0
                if expected_revision != revision:
                    raise CampaignConflict("Campaign changed elsewhere. Reload it before saving.")
                original_template = (
                    CampaignSpec.model_validate_json(row["spec"]).template_id if row else identifier
                )
                if spec.template_id != original_template:
                    raise ValueError("Create a new campaign to use a different template")
            else:
                revision = 0
            db.execute(
                "INSERT INTO campaigns (id,spec,template_hash,revision) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET spec=excluded.spec,"
                "template_hash=excluded.template_hash,revision=excluded.revision,"
                "updated=CURRENT_TIMESTAMP",
                (
                    identifier,
                    spec.model_dump_json(),
                    definition_hash(template),
                    revision + 1,
                ),
            )
        return self.get(identifier)

    def resolve(self, identifier: str):
        record = self.get(identifier)
        if record["revision"] == 0:
            return self.templates[identifier], None
        spec = record["spec"]
        template = self.templates.get(spec.template_id)
        if template is None or definition_hash(template) != record["template_hash"]:
            raise CampaignConflict("Template changed. Review and save this campaign again.")
        policy = CampaignPolicy(
            spec=spec, template_hash=record["template_hash"], revision=record["revision"]
        )
        return compile_campaign(template, spec, identifier), policy

    def identifiers(self) -> list[str]:
        with self.connection() as db:
            saved = [
                row["id"] for row in db.execute("SELECT id FROM campaigns ORDER BY updated DESC")
            ]
        return list(dict.fromkeys([*self.templates, *saved]))

    def definitions(self) -> dict:
        return {identifier: self.resolve(identifier)[0] for identifier in self.identifiers()}

    def options(self) -> list[dict]:
        options = []
        for identifier in self.identifiers():
            definition, policy = self.resolve(identifier)
            search = definition.search
            options.append(
                {
                    "id": identifier,
                    "name": definition.name,
                    "revision": policy.revision if policy else 0,
                    "controlled_test": bool(
                        policy and policy.spec.template_id == "zz-controlled-queue-test.yaml"
                    ),
                    "criteria": "Search: "
                    + (", ".join(search.keywords.include[:3]) or "Campaign criteria")
                    + " · Location: "
                    + (", ".join(search.locations) or "Any location"),
                    "default_profiles": policy.spec.profile_limit
                    if policy
                    else min(5, definition.limits.max_results),
                    "default_pages": policy.spec.page_limit
                    if policy
                    else min(3, definition.limits.max_pages),
                    "profile_limit": min(100, definition.limits.max_results),
                    "page_limit": min(5, definition.limits.max_pages),
                    "queries": [
                        {"term": term, "page": page}
                        for term, page in build_people_search_steps(definition)[:5]
                    ],
                }
            )
        return options
