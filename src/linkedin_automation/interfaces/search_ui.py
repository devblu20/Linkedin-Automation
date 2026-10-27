"""The automatic search form shared by local and hosted dashboards."""

# ruff: noqa: E501 - embedded dashboard asset.

SEARCH_PANEL = """<section class="panel" id="automaticSearch">
<form id="searchForm" class="toolbar">
<div class="search"><label for="searchCampaign">Find matching LinkedIn profiles</label><select id="searchCampaign" aria-label="Search campaign" required style="width:100%"><option value="">Choose campaign</option></select></div>
<div><label for="searchLimit">Profile limit</label><input id="searchLimit" type="number" min="1" max="25" value="5" required style="width:90px;padding:10px;border:1px solid #d0d5dd;border-radius:8px"></div>
<div><label for="searchPages">Search pages</label><input id="searchPages" type="number" min="1" max="5" value="3" required style="width:75px;padding:10px;border:1px solid #d0d5dd;border-radius:8px"></div>
<button class="btn primary" id="searchButton" type="submit">Search and fetch</button>
</form><p id="searchCriteria" style="padding:0 19px;margin:0">Select a campaign to see its search criteria.</p>
<p id="workerStatus" role="status" style="padding:10px 19px;margin:0">Checking browser worker…</p>
<p id="searchDraft" role="status" style="padding:0 19px 10px;margin:0">Profile limit is a maximum to read, not a guaranteed number of saved leads. No outreach is sent.</p>
<p id="searchRunLabel" style="padding:0 19px 5px;margin:0;font-weight:700"></p>
<p id="searchResult" role="status" style="padding:0 19px 16px;margin:0">No search run selected yet.</p>
</section>"""

SEARCH_SCRIPT = """<script>
const searchForm=document.getElementById('searchForm'),searchButton=document.getElementById('searchButton'),searchResult=document.getElementById('searchResult'),searchCampaign=document.getElementById('searchCampaign'),workerStatus=document.getElementById('workerStatus'),searchDraft=document.getElementById('searchDraft');let campaignOptions=[],submitting=false;
function showSearchJob(job){
if(!job)return;const result=job.result||{},requested=job.requested||{};
document.getElementById('searchRunLabel').textContent=`Last search: ${job.campaign} · limit ${requested.profiles??'?'} profiles · up to ${requested.pages??'?'} pages · run ${job.id.slice(0,8)}`;
let text='';
if(job.status==='queued')text='Search queued. It will start when your browser worker is available.';
else if(job.status==='running')text='Searching LinkedIn and reading profiles. Please leave the worker browser open.';
else if(job.status==='completed')text=`Completed: ${result.observed||0} profiles read, ${result.accepted||0} saved, ${result.rejected||0} rejected. ${result.message||''}`;
else text=result.message||'Search stopped. Check browser access and retry.';
if(result.pages_visited)text+=` Checked ${result.pages_visited} search pages.`;
if(result.hidden_result_cards)text+=` LinkedIn hid ${result.hidden_result_cards} result cards (may repeat across searches).`;
const reasons=Object.entries(result.rejection_counts||{}).map(([reason,count])=>`${reason}: ${count}`);if(reasons.length)text+=' Rejection reasons — '+reasons.join('; ')+'.';
searchResult.textContent=text;
}
async function refreshSearch(){try{const r=await fetch('/api/search/settings');if(!r.ok)throw Error('Cannot load search settings');const state=await r.json();const selected=searchCampaign.value;if(JSON.stringify(state.campaigns)!==JSON.stringify(campaignOptions)){campaignOptions=state.campaigns;searchCampaign.replaceChildren(new Option('Choose campaign',''));for(const item of campaignOptions)searchCampaign.add(new Option(item.name,item.id));searchCampaign.value=selected;}workerStatus.textContent=state.mode==='local'?'Uses your saved LinkedIn login on this computer.':state.worker_online?'Browser worker online — ready to run searches.':'Browser worker offline. Searches will wait until it is started on your computer.';const active=(state.jobs||[]).some(j=>['queued','running'].includes(j.status));searchButton.disabled=submitting||active||!campaignOptions.length;if(state.jobs?.length)showSearchJob(state.jobs[0]);if(!active)await Promise.all([load(),loadSummary()]);}catch(error){workerStatus.textContent=error.message;}}
function changedSearch(){searchDraft.textContent='Settings changed. Click Search and fetch to start a new run; the last result below is unchanged.';}
searchCampaign.onchange=()=>{const item=campaignOptions.find(x=>x.id===searchCampaign.value);document.getElementById('searchCriteria').textContent=item?.criteria||'Campaign rules determine which profiles are accepted.';changedSearch();};
document.getElementById('searchLimit').oninput=changedSearch;document.getElementById('searchPages').oninput=changedSearch;
searchForm.onsubmit=async e=>{e.preventDefault();submitting=true;searchButton.disabled=true;searchDraft.textContent='Starting automatic LinkedIn search…';try{const r=await fetch('/api/search/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campaign:searchCampaign.value,max_results:Number(document.getElementById('searchLimit').value),max_pages:Number(document.getElementById('searchPages').value)})});const job=await r.json();if(!r.ok)throw Error(typeof job.detail==='string'?job.detail:'Check campaign, profile limit and search pages');showSearchJob(job);searchDraft.textContent='Search submitted. The selected profile limit is a maximum, not a guaranteed number of matches.';}catch(error){searchDraft.textContent=error.message;}finally{submitting=false;await refreshSearch();}};
refreshSearch();setInterval(refreshSearch,5000);
</script>"""
