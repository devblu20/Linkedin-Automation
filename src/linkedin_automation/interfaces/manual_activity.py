"""Explicit human attestations and manual outreach records; never sends to LinkedIn."""

# ruff: noqa: E501 - embedded dashboard script.

from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ManualActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    action: Literal["evidence_checked", "connection_sent", "connected", "message_sent"]
    expected_status: str = Field(max_length=32)
    note: str = Field(min_length=10, max_length=5000)
    source_url: str = Field(default="", max_length=2048)
    confirmed: Literal[True]
    profile_updates: dict[Literal["company", "current_title", "industry", "experience"], str] = (
        Field(default_factory=dict, max_length=4)
    )

    @field_validator("profile_updates")
    @classmethod
    def bounded_profile_fields(cls, values):
        if any(
            len(value) > (10000 if key == "experience" else 255) for key, value in values.items()
        ):
            raise ValueError("Profile field exceeds its supported length")
        if len(values.get("experience", "").splitlines()) > 100:
            raise ValueError("Experience supports at most 100 entries")
        return {key: value.strip() for key, value in values.items() if value.strip()}

    @field_validator("source_url")
    @classmethod
    def source(cls, value):
        value = value.strip()
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or any(c.isspace() for c in value)
            ):
                raise ValueError("Use a public HTTP(S) source URL without credentials")
        return value

    @field_validator("note")
    @classmethod
    def note_text(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("Add a meaningful activity note")
        return value.strip()


def install_manual_activity(app, repository, review, scope_check):
    @app.get("/api/leads/{lead_id}/activities")
    def history(lead_id: UUID):
        scope_check(lead_id)
        return repository.list_manual_activities(lead_id)

    @app.get("/api/leads/{lead_id}/activities/export.csv")
    def export_history(lead_id: UUID):
        scope_check(lead_id)
        from linkedin_automation.interfaces.report_exports import report_response

        items = repository.list_manual_activities(lead_id)
        if len(items) > 5000:
            raise HTTPException(413, "Activity history exceeds 5000 rows")
        keys = (
            "created_at",
            "action",
            "from_status",
            "to_status",
            "note",
            "source_url",
            "draft_snapshot",
        )
        return report_response(
            keys,
            [[str(item[key]) for key in keys] for item in items],
            "manual-activity",
            "csv",
            {"Lead": str(lead_id)},
        )

    @app.post("/api/leads/{lead_id}/activities")
    def record(lead_id: UUID, payload: ManualActivity):
        scope_check(lead_id)
        queue=getattr(app.state,"delivery_queue",None)
        if queue and queue.active_for(lead_id):
            raise HTTPException(409,"Automated delivery is active. Cancel an unstarted delivery or wait for its result before recording manual activity.")
        if payload.action == "evidence_checked" and not payload.source_url:
            raise HTTPException(422, "Supply the source you personally checked")
        if payload.profile_updates and payload.action != "evidence_checked":
            raise HTTPException(422, "Profile supplements require an evidence-check record")
        if payload.action == "connection_sent":
            assessment = review.assessment(lead_id)
            if assessment["blockers"]:
                raise HTTPException(422, "Qualification must pass before recording outreach")
        try:
            repository.record_manual_activity(lead_id, payload.model_dump(mode="json"))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"ok": True, "recorded_manually": True, "sent_by_app": False}


MANUAL_ACTIVITY_SCRIPT = r"""<script>
document.addEventListener('DOMContentLoaded',()=>{
const dialog=document.createElement('dialog');dialog.id='activityDialog';dialog.innerHTML='<form id="activityForm"><div class="modal-head"><div><h2 id="activityTitle">Record manual activity</h2><p class="sub">This records your confirmation. The app does not send anything or independently verify LinkedIn activity.</p></div><button class="close" id="closeActivity" type="button" aria-label="Close activity">x</button></div><div class="form-grid"><div class="field full"><label for="activityAction">Activity</label><select id="activityAction"></select></div><div class="field full"><label for="activitySource">Source or LinkedIn URL</label><input id="activitySource" type="url" maxlength="2048" placeholder="https://"></div><div class="field full"><label for="activityNote">What did you verify or do?</label><textarea id="activityNote" minlength="10" maxlength="5000" required></textarea></div><div class="field full"><label><input id="activityConfirmed" type="checkbox" required style="width:auto"> I personally checked this evidence or completed this activity.</label></div><div class="field full"><strong>Activity history</strong><div id="activityHistory" role="status"></div></div></div><div class="modal-actions"><span class="sub" role="status" id="activityResult"></span><button type="submit" class="btn primary" id="saveActivity">Record activity</button></div></form>';document.body.append(dialog);
const el=id=>document.getElementById(id);let selected=null,submission=null;const supplementBox=document.createElement('div');supplementBox.className='field full';supplementBox.id='profileSupplements';el('activityHistory').parentElement.before(supplementBox);function supplements(){supplementBox.replaceChildren();supplementBox.hidden=el('activityAction').value!=='evidence_checked';if(supplementBox.hidden||!selected)return;if(['connection_sent','connected','message_ready','message_sent'].includes(selected.outreach.status)){supplementBox.hidden=true;return;}const intro=document.createElement('p');intro.className='sub';intro.textContent='Optional: fill only missing profile fields from the source you personally checked. This returns the lead to Needs verification.';supplementBox.append(intro);for(const [key,title] of [['company','Company'],['current_title','Current role'],['industry','Industry'],['experience','Experience']]){const old=selected.lead[key];if(Array.isArray(old)?old.length:old)continue;const wrap=document.createElement('div');wrap.className='field';const label=document.createElement('label'),input=document.createElement(key==='experience'?'textarea':'input');input.id='supplement-'+key;input.dataset.supplement=key;input.maxLength=key==='experience'?10000:255;label.htmlFor=input.id;label.textContent=title+' (missing; optional)';wrap.append(label,input);supplementBox.append(wrap)}}el('activityAction').onchange=supplements;
async function openActivity(id){selected=leads.find(x=>x.lead.id===id);if(!selected)return;submission=null;el('activityForm').reset();el('activityTitle').textContent=selected.lead.full_name+' — manual activity';el('activityResult').textContent='';el('activitySource').value=selected.lead.profile_url;const actions=el('activityAction');actions.replaceChildren(new Option('Evidence checked','evidence_checked'));const next={approved:['connection_sent','Connection request sent manually'],connection_queued:['connection_sent','Connection request sent manually'],connection_sent:['connected','Connection accepted on LinkedIn'],connected:['message_sent','Follow-up message sent manually'],message_ready:['message_sent','Follow-up message sent manually']}[selected.outreach.status];if(next)actions.add(new Option(next[1],next[0]));supplements();dialog.showModal();el('activityHistory').textContent='Loading…';try{const r=await fetch('/api/leads/'+id+'/activities');if(!r.ok)throw Error();const items=await r.json();if(selected?.lead.id!==id)return;const history=el('activityHistory');history.replaceChildren();const download=document.createElement('a');download.href='/api/leads/'+id+'/activities/export.csv';download.className='icon-btn';download.textContent='Download history CSV';download.download='';history.append(download);for(const item of items){const p=document.createElement('p');p.textContent=new Date(item.created_at).toLocaleString()+' · '+item.action.replaceAll('_',' ')+' (recorded manually): '+item.note;if(item.source_url){const a=document.createElement('a');a.href=item.source_url;a.target='_blank';a.rel='noopener';a.textContent=' Source';p.append(a)}history.append(p)}if(!items.length){const empty=document.createElement('p');empty.textContent='No recorded activity yet.';history.append(empty)}}catch{el('activityHistory').textContent='Could not load activity history.'}}
window.openManualActivity=openActivity;el('closeActivity').onclick=()=>dialog.close();
el('activityForm').onsubmit=async e=>{e.preventDefault();const button=el('saveActivity');button.disabled=true;const values={action:el('activityAction').value,expected_status:selected.outreach.status,note:el('activityNote').value,source_url:el('activitySource').value,confirmed:el('activityConfirmed').checked,profile_updates:el('activityAction').value==='evidence_checked'?Object.fromEntries([...supplementBox.querySelectorAll('[data-supplement]')].filter(x=>x.value.trim()).map(x=>[x.dataset.supplement,x.value])):{}};const signature=JSON.stringify(values);if(!submission||submission.signature!==signature)submission={signature,id:crypto.randomUUID()};try{const r=await fetch('/api/leads/'+selected.lead.id+'/activities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:submission.id,...values})});const body=await r.json();if(!r.ok)throw Error(typeof body.detail==='string'?body.detail:'Check source, note and confirmation');dialog.close();notice('Manual activity recorded. The app sent nothing.');await Promise.all([load(),loadSummary()]);}catch(error){el('activityResult').textContent=error.message}finally{button.disabled=false}};

const baseQualification=renderQualification;renderQualification=function(data){baseQualification(data);const box=el('qualificationSummary');if(!box)return;const p=document.createElement('p');p.textContent='Profile data coverage: '+(data.data_coverage||[]).filter(x=>x.available).length+'/'+(data.data_coverage||[]).length+' fields available. Last profile observation: '+new Date(data.profile_observed_at).toLocaleString()+'.';const missing=document.createElement('p');missing.className='sub';missing.textContent='Evidence and profile supplements are reviewer supplied; missing facts are never guessed. Original collected observations remain in research history.';const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='Record evidence / fill missing fields';button.onclick=()=>{const id=editing.lead.id;editor.close();openActivity(id)};if(data.automatic_review_blockers?.length){const hold=document.createElement('p');hold.textContent='Automatic review: '+data.automatic_review_blockers.join('; ');box.append(hold)}box.append(p,missing,button)};

});
</script>"""
