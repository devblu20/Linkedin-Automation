"""Authenticated, filtered downloads built from current records without stored paths."""

import csv
import io
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import evaluate_candidate
from linkedin_automation.infrastructure.reporting.excel import escape_cell
from linkedin_automation.interfaces.observation_view import explain_rejection

MAX_EXPORT_ROWS = 5000
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PROFILE_COLUMNS = (
    "Full name",
    "Headline",
    "Current role",
    "Company",
    "Location",
    "Industry",
    "Company size",
    "Self employed",
    "About",
    "Experience",
    "Education",
    "Skills",
    "Connections",
    "Followers",
    "LinkedIn URL",
)


def profile_values(profile):
    return [
        profile.full_name,
        profile.headline,
        profile.current_title,
        profile.company,
        profile.location,
        profile.industry,
        f"{profile.company_size_min or 1}-{profile.company_size_max}"
        if profile.company_size_max is not None
        else "",
        "Unknown" if profile.self_employed is None else "Yes" if profile.self_employed else "No",
        profile.about,
        " | ".join(profile.experience),
        " | ".join(profile.education),
        " | ".join(profile.skills),
        profile.connections,
        profile.followers,
        profile.profile_url,
    ]


def safe_value(value):
    if isinstance(value, str):
        return escape_cell(_INVALID_XML.sub("", value))
    return value


def report_response(headers, rows, dataset, file_format, filters):
    generated = datetime.now(UTC)
    filename = f"linkedin-{dataset}-{generated:%Y%m%d-%H%M%S}.{file_format}"
    rows = [[safe_value(value) for value in row] for row in rows]
    if file_format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        content = output.getvalue().encode("utf-8-sig")
        media_type = "text/csv; charset=utf-8"
    else:
        if any(isinstance(v, str) and len(v) > 32767 for row in rows for v in row):
            raise HTTPException(422, "A field exceeds Excel's cell limit. Download CSV instead.")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Saved leads" if dataset == "leads" else "Collected profiles"
        sheet.append(list(headers))
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            sheet.column_dimensions[cell.column_letter].width = 24
        summary = workbook.create_sheet("Export scope")
        summary.append(["Field", "Value"])
        for key, value in {
            "Dataset": dataset,
            "Exported rows": len(rows),
            "Generated at (UTC)": generated.isoformat(),
            **filters,
        }.items():
            summary.append([key, safe_value(value)])
        summary.column_dimensions["A"].width = 25
        summary.column_dimensions["B"].width = 70
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        content = output.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Export-Row-Count": str(len(rows)),
            "X-Content-Type-Options": "nosniff",
        },
    )


def install_report_exports(app: FastAPI, repository, scope: UUID | None):
    def selected_run(dataset, run_id, decision, status, q):
        if scope and run_id and scope != run_id:
            raise HTTPException(404, "Run is outside this review page")
        selected = scope or run_id
        if selected and repository.get_run(selected) is None:
            raise HTTPException(404, "Run not found")
        if dataset == "observations" and (status is not None or q):
            raise HTTPException(
                422, "Use run and collection decision filters for collected profiles"
            )
        if dataset == "leads" and decision != "all":
            raise HTTPException(422, "Use review status and search filters for saved leads")
        return selected

    def matches_lead(lead, record, status, q):
        if status and (record is None or record.status != status):
            return False
        return (
            q.casefold()
            in " ".join(
                (lead.full_name, lead.company, lead.current_title, lead.headline, lead.location)
            ).casefold()
        )

    @app.get("/api/reports/analytics")
    def report_analytics(request: Request, run_id: UUID | None = None):
        from linkedin_automation.application.engagement import analytics

        selected = selected_run("leads", run_id, "all", None, "")
        # Local research-only pages have no synced inbox. Hosted workspaces reuse their
        # existing engagement store, so the same metrics work without an AI API call.
        store = getattr(app.state, "engagement", SimpleNamespace(history=lambda **_: []))
        sender = (request.scope.get("state", {}).get("cloud_delivery_account") or {}).get(
            "account_id"
        )
        return analytics(repository, store, selected, sender)

    @app.get("/api/reports/preview")
    def preview(
        dataset: Literal["leads", "observations"] = "leads",
        run_id: UUID | None = None,
        decision: Literal["all", "accepted", "rejected"] = "all",
        status: OutreachStatus | None = None,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=10, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        selected = selected_run(dataset, run_id, decision, status, q)
        if dataset == "leads":
            outreach = {record.lead_id: record for record in repository.list_outreach(selected)}
            matches = [
                lead
                for lead in repository.list_leads(selected)
                if matches_lead(lead, outreach.get(lead.id), status, q)
            ]
            total = len(matches)
            columns = [
                ("name", "Lead"),
                ("role", "Role"),
                ("company", "Company"),
                ("location", "Location"),
                ("score", "Score"),
                ("status", "Review status"),
            ]
            items = []
            for lead in matches[offset : offset + limit]:
                record = outreach.get(lead.id)
                items.append(
                    {
                        "name": lead.full_name,
                        "role": lead.current_title or lead.headline,
                        "company": lead.company or "Unknown company",
                        "location": lead.location or "Not collected",
                        "score": f"{record.score}/100" if record else "—",
                        "status": record.status.value if record else "unknown",
                    }
                )
        else:
            counts = repository.observation_counts(selected)
            total = counts["read" if decision == "all" else decision]
            records = repository.list_observations(
                selected,
                accepted=None if decision == "all" else decision == "accepted",
                limit=limit,
                offset=offset,
            )
            columns = [
                ("name", "Profile"),
                ("role", "Role"),
                ("company", "Company"),
                ("location", "Location"),
                ("decision", "Decision"),
                ("reason", "Reason"),
            ]
            items = [
                {
                    "name": item.candidate.full_name,
                    "role": item.candidate.current_title or item.candidate.headline,
                    "company": item.candidate.company or "Unknown company",
                    "location": item.candidate.location or "Not collected",
                    "decision": "accepted" if item.accepted else "rejected",
                    "reason": "; ".join(explain_rejection(item.rejection_reason)) or "—",
                }
                for item in records
            ]
        return {
            "dataset": dataset,
            "run_id": str(selected) if selected else None,
            "columns": [{"key": key, "label": title} for key, title in columns],
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    @app.get("/api/exports/{dataset}.{file_format}")
    def download(
        dataset: Literal["leads", "observations"],
        file_format: Literal["csv", "xlsx"],
        run_id: UUID | None = None,
        decision: Literal["all", "accepted", "rejected"] = "all",
        status: OutreachStatus | None = None,
        q: str = Query(default="", max_length=200),
    ):
        selected = selected_run(dataset, run_id, decision, status, q)
        runs = {}

        def run(identifier):
            if identifier not in runs:
                runs[identifier] = repository.get_run(identifier)
            return runs[identifier]

        if dataset == "observations":
            records = repository.list_observations(
                selected,
                accepted=None if decision == "all" else decision == "accepted",
                limit=MAX_EXPORT_ROWS + 1,
            )
            if len(records) > MAX_EXPORT_ROWS:
                raise HTTPException(413, "Too many rows. Select a research run before exporting.")
            headers = (
                "Observation ID",
                "Run ID",
                "Campaign",
                *PROFILE_COLUMNS,
                "Collection decision",
                "Rejection reasons",
                "Matched criteria",
                "Collected at (UTC)",
                "Research source",
            )
            rows = []
            for record in records:
                research = run(record.run_id)
                definition = (
                    SearchDefinition.model_validate_json(research.definition_json)
                    if research
                    else None
                )
                evidence = evaluate_candidate(record.candidate, definition) if definition else None
                rows.append(
                    [
                        str(record.id),
                        str(record.run_id),
                        research.search_name if research else "",
                        *profile_values(record.candidate),
                        "accepted" if record.accepted else "rejected",
                        "; ".join(explain_rejection(record.rejection_reason)),
                        " | ".join(evidence.matched) if evidence else "",
                        record.candidate.collected_at.astimezone(UTC).isoformat(),
                        record.candidate.source_search,
                    ]
                )
        else:
            records = repository.list_leads(selected)
            outreach = {record.lead_id: record for record in repository.list_outreach(selected)}
            rows = []
            headers = (
                "Lead ID",
                "Run ID",
                "Campaign",
                *PROFILE_COLUMNS,
                "Matched criteria",
                "Qualification score",
                "Review status",
                "Firm type",
                "Funds (GBP)",
                "Funds evidence URL",
                "Evidence notes",
                "Connection draft",
                "Follow-up draft",
                "Review updated at (UTC)",
                "Research source",
            )
            for lead in records:
                record = outreach.get(lead.id)
                if not matches_lead(lead, record, status, q):
                    continue
                if len(rows) >= MAX_EXPORT_ROWS:
                    raise HTTPException(413, "Too many rows. Narrow the filters before exporting.")
                research = run(lead.run_id)
                rows.append(
                    [
                        str(lead.id),
                        str(lead.run_id),
                        research.search_name if research else "",
                        *profile_values(lead),
                        " | ".join(lead.evidence.matched),
                        record.score if record else None,
                        record.status.value if record else "",
                        record.firm_type if record else "",
                        record.funds_gbp if record else None,
                        record.fund_evidence_url if record else "",
                        record.fund_evidence_notes if record else "",
                        record.connection_message if record else "",
                        record.follow_up_message if record else "",
                        record.updated_at.astimezone(UTC).isoformat() if record else "",
                        lead.source_search,
                    ]
                )
        return report_response(
            headers,
            rows,
            dataset,
            file_format,
            {
                "Run": str(selected) if selected else "All runs",
                "Collection decision": decision,
                "Review status": status.value if status else "All statuses",
                "Search text": q,
                "Rows": "All matching rows across pages; maximum 5000",
            },
        )


# ruff: noqa: E501 - embedded dashboard script.
EXPORT_SCRIPT = """<script>
async function downloadReport(dataset,format,button){button.disabled=true;try{const query=new URLSearchParams();if(dataset==='observations'){if(obsRun.value)query.set('run_id',obsRun.value);query.set('decision',obsDecision.value)}else{const status=document.getElementById('statusFilter').value,q=document.getElementById('search').value;if(status)query.set('status',status);if(q)query.set('q',q)}const response=await fetch(`/api/exports/${dataset}.${format}?${query}`);if(!response.ok){const error=await response.json();throw Error(typeof error.detail==='string'?error.detail:'Check export filters')}const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`linkedin-${dataset}.${format}`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notice(`Downloaded ${response.headers.get('X-Export-Row-Count')} matching rows.`)}catch(error){notice('Export failed: '+error.message)}finally{button.disabled=false}}
for(const dataset of ['leads','observations'])for(const format of ['csv','xlsx'])document.getElementById(`export-${dataset}-${format}`).onclick=e=>downloadReport(dataset,format,e.currentTarget);
</script>"""


def export_buttons(dataset):
    return f"""<button class="btn" id="export-{dataset}-csv" type="button" title="Download all rows matching these filters, across pages">Download CSV</button><button class="btn" id="export-{dataset}-xlsx" type="button" title="Download all rows matching these filters, across pages">Download Excel</button>"""
