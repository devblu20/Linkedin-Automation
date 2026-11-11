"""Bounded, manually authored qualification policies; no executable configuration."""

from string import Formatter

from pydantic import BaseModel, ConfigDict, Field, field_validator

from linkedin_automation.application.campaigns import CampaignSpec, Term
from linkedin_automation.domain.search_definition import (
    QualificationConfiguration,
    SearchDefinition,
)


class ManualQualification(QualificationConfiguration):
    firm_types: list[Term] = Field(min_length=1, max_length=30)
    executive_title_priority: list[Term] = Field(min_length=1, max_length=30)
    self_employment_keywords: list[Term] = Field(default_factory=list, max_length=30)


class ManualTemplateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Term = Field(max_length=100)
    spec: CampaignSpec
    qualification: ManualQualification
    connection_template: str = Field(min_length=1, max_length=2000)
    message_template: str = Field(min_length=1, max_length=6000)

    @field_validator("connection_template", "message_template")
    @classmethod
    def safe_placeholders(cls, value):
        if not value.strip():
            raise ValueError("A message draft cannot be blank")
        try:
            for _, field, format_spec, conversion in Formatter().parse(value):
                if field is not None and (
                    field not in {"first_name", "full_name", "company"} or format_spec or conversion
                ):
                    raise ValueError(
                        "Use only {first_name}, {full_name} and {company} placeholders"
                    )
        except ValueError as error:
            raise ValueError("Invalid message placeholder: " + str(error)) from None
        return value.strip()


def manual_definition(base: SearchDefinition, payload: ManualTemplateWrite, identifier: str):
    from linkedin_automation.application.campaigns import compile_campaign

    # Search defaults reuse the campaign form; all collection/sending ceilings stay server-owned.
    definition = compile_campaign(base, payload.spec, payload.spec.template_id).model_dump()
    definition.update(
        name=payload.name,
        qualification=payload.qualification.model_dump(),
        limits=base.limits.model_dump(),
        outreach={
            "enabled": base.outreach.enabled,
            "manual_send_only": True,
            "connection_template": payload.connection_template,
            "message_template": payload.message_template,
        },
        output={
            "file_name": identifier.removesuffix(".yaml") + ".xlsx",
            "drive_folder_id_env": base.output.drive_folder_id_env,
        },
    )
    return SearchDefinition.model_validate(definition)
