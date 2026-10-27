"""Read-only collected-profile history, including campaign rejection evidence."""

# ruff: noqa: E501 - embedded dashboard asset.

from dataclasses import asdict
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query

from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import evaluate_candidate
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository


def explain_rejection(reason: str | None) -> list[str]:
    messages = []
    for code in (reason or "").split(";"):
        if code.startswith("required:"):
            messages.append("Missing required " + code.split(":", 1)[1])
        elif code.startswith("title:"):
            messages.append("Excluded title: " + code.split(":", 1)[1])
        elif code.startswith("keyword:"):
            messages.append("Excluded keyword: " + code.split(":", 1)[1])
        elif code.startswith("company_size:"):
            messages.append("Company exceeds campaign size limit")
        elif code:
            messages.append(
                "Did not match campaign criteria"
                if code in {"criteria_not_matched_or_excluded", "no_positive_criteria_matched"}
                else "Profile data needs review"
            )
    return list(dict.fromkeys(messages))


def install_observation_routes(
    app: FastAPI, repository: DatabaseResearchRepository, scope: UUID | None
):
    @app.get("/api/research/runs")
    def runs():
        items = [repository.get_run(scope)] if scope else repository.list_runs()
        return [
            {
                "id": str(item.id),
                "campaign": item.search_name,
                "created_at": item.created_at,
                "status": item.status.value,
                "read": item.counters.observed,
                "accepted": item.counters.accepted,
                "rejected": item.counters.rejected,
            }
            for item in items
            if item is not None
        ]

    @app.get("/api/observations")
    def observations(
        decision: Literal["all", "accepted", "rejected"] = "all",
        run_id: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        if scope and run_id and scope != run_id:
            raise HTTPException(404, "Run is outside this review page")
        selected = scope or run_id
        accepted = None if decision == "all" else decision == "accepted"
        collected = repository.list_observations(
            selected, accepted=accepted, limit=limit + 1, offset=offset
        )
        names = {}
        definitions = {}
        items = []
        for item in collected[:limit]:
            if item.run_id not in names:
                run = repository.get_run(item.run_id)
                names[item.run_id] = run.search_name if run else "Unknown campaign"
                definitions[item.run_id] = (
                    SearchDefinition.model_validate_json(run.definition_json) if run else None
                )
            profile = asdict(item.candidate)
            profile.pop("profile_snapshot", None)
            definition = definitions[item.run_id]
            evidence = evaluate_candidate(item.candidate, definition) if definition else None
            items.append(
                {
                    "id": str(item.id),
                    "run_id": str(item.run_id),
                    "campaign": names[item.run_id],
                    "profile": profile,
                    "decision": "accepted" if item.accepted else "rejected",
                    "reasons": explain_rejection(item.rejection_reason),
                    "matching_evidence": list(evidence.matched) if evidence else [],
                    "missing_fields": [
                        label
                        for key, label in {
                            "company": "Company",
                            "current_title": "Current role",
                            "industry": "Industry",
                            "about": "About",
                            "experience": "Experience",
                        }.items()
                        if not profile[key]
                    ],
                }
            )
        return {
            "items": items,
            "counts": repository.observation_counts(selected),
            "offset": offset,
            "limit": limit,
            "has_more": len(collected) > limit,
        }


OBSERVATION_PANEL = """<section class="panel" id="collectedPanel">
<div class="toolbar"><div class="search"><strong>Collected profiles</strong><div class="sub">Profiles read from LinkedIn, including campaign rejections.</div></div>
<select id="observationRun" aria-label="Collected profile run"><option value="">All runs</option></select>
<select id="observationDecision" aria-label="Collection decision"><option value="all">All collected</option><option value="rejected">Rejected</option><option value="accepted">Accepted</option></select>
<button class="btn" id="refreshObservations" type="button">Refresh profiles</button></div>
<p id="observationSummary" role="status" style="padding:0 19px;margin:12px 0"></p>
<div class="table-wrap"><table><thead><tr><th>Profile</th><th>Location</th><th>Campaign / run</th><th>Decision</th><th>Reason / missing data</th><th>Details</th></tr></thead><tbody id="observationRows"></tbody></table></div>
<p id="observationEmpty" class="empty">Loading collected profiles…</p>
<div class="toolbar"><button class="btn" id="previousObservations" type="button">Previous</button><span id="observationPage"></span><button class="btn" id="nextObservations" type="button">Next</button></div>
</section>
<dialog id="observationDialog"><div class="modal-head"><h2>Collected profile details</h2><button class="close" id="closeObservation" type="button" aria-label="Close collected profile">x</button></div><div id="observationDetails" class="form-grid"></div></dialog>"""

OBSERVATION_SCRIPT = """<script>
let observationOffset=0,observationItems=[],observationRequestSerial=0;const observationLimit=25;
const obsRows=document.getElementById('observationRows'),obsEmpty=document.getElementById('observationEmpty'),obsRun=document.getElementById('observationRun'),obsDecision=document.getElementById('observationDecision');
function obsCell(row,text){const cell=document.createElement('td');cell.textContent=text;row.appendChild(cell);return cell}
function showObservation(item){const panel=document.getElementById('observationDetails');panel.replaceChildren();const values={Name:item.profile.full_name,Headline:item.profile.headline,Location:item.profile.location,Company:item.profile.company,'Current title':item.profile.current_title,Industry:item.profile.industry,About:item.profile.about,Experience:item.profile.experience,Education:item.profile.education,Skills:item.profile.skills,Connections:item.profile.connections,Followers:item.profile.followers,Decision:item.decision,'Matched criteria':item.matching_evidence,Reasons:item.reasons,'Matching fields':'Current headline, role, company, location and industry. Featured posts and suggested profiles are excluded.','Missing fields':item.missing_fields,'Collected at':item.profile.collected_at};for(const [title,value] of Object.entries(values)){const box=document.createElement('div');box.className='field full';const label=document.createElement('label'),text=document.createElement('div');label.textContent=title;text.className='profile-value';text.textContent=Array.isArray(value)?value.join(' | ')||'Not collected':value||'Not collected';box.append(label,text);panel.appendChild(box)}document.getElementById('observationDialog').showModal()}
async function loadObservations(){const requestId=++observationRequestSerial;try{const query=new URLSearchParams({decision:obsDecision.value,limit:String(observationLimit),offset:String(observationOffset)});if(obsRun.value)query.set('run_id',obsRun.value);const response=await fetch('/api/observations?'+query);if(!response.ok)throw Error('Could not load collected profiles');const data=await response.json();if(requestId!==observationRequestSerial)return;observationItems=data.items;obsRows.replaceChildren();for(const item of observationItems){const row=document.createElement('tr'),person=obsCell(row,'');const name=document.createElement('strong'),headline=document.createElement('div');name.textContent=item.profile.full_name;headline.textContent=item.profile.headline||'Headline not collected';headline.className='sub';person.append(name,headline);obsCell(row,item.profile.location||'Not collected');obsCell(row,item.campaign+' · '+item.run_id.slice(0,8));obsCell(row,item.decision);obsCell(row,[item.reasons.join('; '),item.missing_fields.length?'Not collected: '+item.missing_fields.join(', '):''].filter(Boolean).join(' · ')||'Matches campaign criteria');const actions=obsCell(row,'');const button=document.createElement('button');button.className='icon-btn';button.textContent='View details';button.onclick=()=>showObservation(item);actions.append(button);obsRows.append(row)}const counts=data.counts;document.getElementById('observationSummary').textContent=`${counts.read} collected · ${counts.accepted} accepted · ${counts.rejected} rejected in this scope. Rejected entries are kept for inspection.`;obsEmpty.style.display=data.items.length?'none':'block';obsEmpty.textContent='No collected profiles in this view yet.';document.getElementById('previousObservations').disabled=observationOffset===0;document.getElementById('nextObservations').disabled=!data.has_more;document.getElementById('observationPage').textContent='Page '+(1+Math.floor(observationOffset/observationLimit));}catch(error){if(requestId!==observationRequestSerial)return;obsEmpty.style.display='block';obsEmpty.textContent=error.message}}
async function loadObservationRuns(){const response=await fetch('/api/research/runs');if(!response.ok)return;const items=await response.json(),selected=obsRun.value;obsRun.replaceChildren(new Option('All runs',''));for(const item of items)obsRun.add(new Option(item.campaign+' · '+new Date(item.created_at).toLocaleString()+' · '+item.id.slice(0,8),item.id));obsRun.value=selected;}
obsRun.onchange=obsDecision.onchange=()=>{observationOffset=0;loadObservations()};document.getElementById('refreshObservations').onclick=()=>Promise.all([loadObservationRuns(),loadObservations()]);document.getElementById('previousObservations').onclick=()=>{observationOffset=Math.max(0,observationOffset-observationLimit);loadObservations()};document.getElementById('nextObservations').onclick=()=>{observationOffset+=observationLimit;loadObservations()};document.getElementById('closeObservation').onclick=()=>document.getElementById('observationDialog').close();Promise.all([loadObservationRuns(),loadObservations()]);setInterval(()=>Promise.all([loadObservationRuns(),loadObservations()]),15000);
</script>"""
