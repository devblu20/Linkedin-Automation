"""Campaign authoring API and shared dashboard editor."""

# ruff: noqa: E501 - embedded dashboard assets.

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from linkedin_automation.application.campaigns import CampaignSpec, spec_from_template
from linkedin_automation.infrastructure.campaign_catalog import CampaignCatalog, CampaignConflict


class CampaignWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: CampaignSpec
    expected_revision: int | None = Field(default=None, ge=0)


def install_campaign_routes(app: FastAPI, catalog: CampaignCatalog, request_guard=None):
    def check(request: Request, write: bool = False):
        if request_guard:
            request_guard(request, write=write)

    @app.get("/api/campaign-templates")
    def templates(request: Request):
        check(request)
        return [
            {
                "id": key,
                "name": value.name,
                "spec": spec_from_template(key, value).model_dump(mode="json"),
            }
            for key, value in catalog.templates.items()
        ]

    @app.get("/api/campaigns/{identifier}")
    def get_campaign(identifier: str, request: Request):
        check(request)
        try:
            return catalog.get(identifier)
        except KeyError:
            raise HTTPException(404, "Campaign not found") from None

    @app.post("/api/campaigns", status_code=201)
    def create_campaign(payload: CampaignWrite, request: Request):
        check(request, True)
        try:
            return catalog.save(payload.spec)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.put("/api/campaigns/{identifier}")
    def update_campaign(identifier: str, payload: CampaignWrite, request: Request):
        check(request, True)
        try:
            return catalog.save(
                payload.spec, identifier=identifier, expected_revision=payload.expected_revision
            )
        except CampaignConflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError:
            raise HTTPException(404, "Campaign not found") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


CAMPAIGN_DIALOG = """<dialog id="campaignDialog"><form id="campaignForm">
<div class="modal-head"><div><h2 id="campaignEditorTitle">Campaign requirements</h2><div class="sub">Changes apply to new searches. Existing runs keep their original rules.</div></div><button type="button" class="close" id="closeCampaign" aria-label="Close campaign editor">x</button></div>
<div class="form-grid">
<div class="field full"><label for="campaignTemplate">Qualification template</label><select id="campaignTemplate" required></select><small class="sub">Keeps the template's qualification and draft policy.</small></div>
<div class="field full"><label for="campaignName">Campaign name</label><input id="campaignName" maxlength="100" required></div>
<div class="field"><label for="campaignTitles">Job titles</label><textarea id="campaignTitles" placeholder="Founder, Owner, Independent Trader"></textarea></div>
<div class="field"><label for="campaignLocations">Locations</label><textarea id="campaignLocations" placeholder="London"></textarea></div>
<div class="field"><label for="campaignKeywords">Include keywords</label><textarea id="campaignKeywords" placeholder="options trading, independent trader"></textarea></div>
<div class="field"><label for="campaignExcludedKeywords">Exclude keywords</label><textarea id="campaignExcludedKeywords" placeholder="recruitment, internship"></textarea></div>
<div class="field"><label for="campaignExcludedTitles">Exclude titles</label><textarea id="campaignExcludedTitles" placeholder="Student, Recruiter"></textarea></div>
<div class="field"><label for="campaignIndustries">Industries</label><textarea id="campaignIndustries"></textarea></div>
<div class="field full"><label for="campaignCompanies">Companies (optional)</label><textarea id="campaignCompanies"></textarea><small class="sub">Separate entries with commas or new lines.</small></div>
<div class="field full"><label>Required matches</label><div style="display:flex;gap:14px;flex-wrap:wrap"><label><input type="checkbox" name="campaignRequired" value="titles" style="width:auto"> Title</label><label><input type="checkbox" name="campaignRequired" value="locations" style="width:auto"> Location</label><label><input type="checkbox" name="campaignRequired" value="keywords" style="width:auto"> Keywords</label><label><input type="checkbox" name="campaignRequired" value="industries" style="width:auto"> Industry</label><label><input type="checkbox" name="campaignRequired" value="companies" style="width:auto"> Company</label></div></div>
<div class="field full"><label>Connection degrees (leave empty for all)</label><div style="display:flex;gap:14px"><label><input type="checkbox" name="campaignDegree" value="1st" style="width:auto"> 1st</label><label><input type="checkbox" name="campaignDegree" value="2nd" style="width:auto"> 2nd</label><label><input type="checkbox" name="campaignDegree" value="3rd" style="width:auto"> 3rd</label></div></div>
<div class="field"><label for="campaignProfileLimit">Profile limit per run (1-25)</label><input id="campaignProfileLimit" type="number" min="1" max="25" required></div>
<div class="field"><label for="campaignPageLimit">Search page limit (1-5)</label><input id="campaignPageLimit" type="number" min="1" max="5" required></div>
<div class="field"><label for="campaignDailyLimit">Daily profile limit</label><input id="campaignDailyLimit" type="number" min="1" max="500" required></div>
</div><div class="modal-actions"><span id="campaignSaveStatus" role="status" class="sub"></span><button class="btn primary" id="saveCampaign" type="submit">Save campaign</button></div>
</form></dialog>"""

CAMPAIGN_SCRIPT = r"""<script>
let campaignEditingId=null,campaignRevision=null,campaignSpec=null,campaignTemplates=[];
const campaignDialog=document.getElementById('campaignDialog'),campaignForm=document.getElementById('campaignForm'),campaignTemplate=document.getElementById('campaignTemplate'),campaignSave=document.getElementById('saveCampaign'),campaignSaveStatus=document.getElementById('campaignSaveStatus');
const campaignInputs={titles:'campaignTitles',locations:'campaignLocations',keywords:'campaignKeywords',excluded_keywords:'campaignExcludedKeywords',excluded_titles:'campaignExcludedTitles',industries:'campaignIndustries',companies:'campaignCompanies'};
function fillCampaign(spec){campaignSpec=spec;campaignTemplate.value=spec.template_id;document.getElementById('campaignName').value=spec.name;for(const [key,id] of Object.entries(campaignInputs))document.getElementById(id).value=(spec[key]||[]).join('\n');for(const el of campaignForm.querySelectorAll('[name="campaignRequired"]'))el.checked=spec.required_dimensions.includes(el.value);for(const el of campaignForm.querySelectorAll('[name="campaignDegree"]'))el.checked=spec.connection_degrees.includes(el.value);document.getElementById('campaignProfileLimit').value=spec.profile_limit;document.getElementById('campaignPageLimit').value=spec.page_limit;document.getElementById('campaignDailyLimit').value=spec.daily_limit;campaignSave.disabled=false;}
function campaignError(body){if(typeof body.detail==='string')return body.detail;if(Array.isArray(body.detail))return body.detail.map(x=>x.msg).slice(0,5).join('; ');return 'Could not save campaign';}
async function openCampaign(edit){campaignEditingId=edit?searchCampaign.value:null;if(edit&&!campaignEditingId){notice('Choose a campaign to edit first');return}campaignRevision=null;campaignSpec=null;campaignForm.reset();campaignSave.disabled=true;campaignSaveStatus.textContent='Loading campaign…';document.getElementById('campaignEditorTitle').textContent=edit?'Edit campaign requirements':'New campaign';campaignDialog.showModal();try{const r=await fetch('/api/campaign-templates');if(!r.ok)throw Error('Could not load campaign templates');campaignTemplates=await r.json();campaignTemplate.replaceChildren();for(const item of campaignTemplates)campaignTemplate.add(new Option(item.name,item.id));campaignTemplate.disabled=edit;if(edit){const detail=await fetch('/api/campaigns/'+encodeURIComponent(campaignEditingId));const body=await detail.json();if(!detail.ok)throw Error(campaignError(body));campaignRevision=body.revision;fillCampaign(body.spec)}else{const selected=campaignTemplates.find(x=>x.id===searchCampaign.value)||campaignTemplates.find(x=>x.id==='bluqq-london-independent-traders.yaml')||campaignTemplates[0];if(!selected)throw Error('No campaign templates available');fillCampaign(selected.spec);document.getElementById('campaignName').value='';}campaignSaveStatus.textContent='Nothing changes until you save.';}catch(error){campaignSaveStatus.textContent=error.message;}}
campaignTemplate.onchange=()=>{const item=campaignTemplates.find(x=>x.id===campaignTemplate.value);if(item)fillCampaign(item.spec)};
document.getElementById('newCampaign').onclick=()=>openCampaign(false);document.getElementById('editCampaign').onclick=()=>openCampaign(true);document.getElementById('closeCampaign').onclick=()=>campaignDialog.close();
campaignForm.onsubmit=async e=>{e.preventDefault();if(!campaignSpec)return;campaignSave.disabled=true;campaignSaveStatus.textContent='Saving…';const spec={...campaignSpec,name:document.getElementById('campaignName').value,template_id:campaignTemplate.value,required_dimensions:[...campaignForm.querySelectorAll('[name="campaignRequired"]:checked')].map(x=>x.value),connection_degrees:[...campaignForm.querySelectorAll('[name="campaignDegree"]:checked')].map(x=>x.value),profile_limit:Number(document.getElementById('campaignProfileLimit').value),page_limit:Number(document.getElementById('campaignPageLimit').value),daily_limit:Number(document.getElementById('campaignDailyLimit').value)};for(const [key,id] of Object.entries(campaignInputs))spec[key]=document.getElementById(id).value.split(/[\n,]+/).map(x=>x.trim()).filter(Boolean);try{const response=await fetch(campaignEditingId?'/api/campaigns/'+encodeURIComponent(campaignEditingId):'/api/campaigns',{method:campaignEditingId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({spec,expected_revision:campaignRevision})});const body=await response.json();if(!response.ok)throw Error(campaignError(body));await refreshSearch();searchCampaign.value=body.id;searchCampaign.dispatchEvent(new Event('change'));campaignDialog.close();notice('Campaign saved. New searches will use these requirements.');}catch(error){campaignSaveStatus.textContent=error.message;}finally{campaignSave.disabled=false;}};
</script>"""
