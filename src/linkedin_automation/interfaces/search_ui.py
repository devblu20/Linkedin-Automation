"""The automatic search form shared by local and hosted dashboards."""

# ruff: noqa: E501 - embedded dashboard asset.

SEARCH_PANEL = """<section class="panel" id="automaticSearch">
<form id="searchForm" class="toolbar">
<div class="search"><label for="searchCampaign">Find matching LinkedIn profiles</label><select id="searchCampaign" aria-label="Search campaign" required style="width:100%"><option value="">Choose campaign</option></select></div>
<div><label for="searchLimit">Profiles to fetch</label><input id="searchLimit" type="number" min="1" max="25" value="5" required style="width:90px;padding:10px;border:1px solid #d0d5dd;border-radius:8px"></div>
<button class="btn primary" id="searchButton" type="submit">Search and fetch</button>
</form><p id="searchCriteria" style="padding:0 19px;margin:0">Select a campaign to see its search criteria.</p>
<p id="workerStatus" role="status" style="padding:10px 19px;margin:0">Checking browser worker…</p>
<p id="searchResult" role="status" style="padding:0 19px 16px;margin:0">Automatically searches LinkedIn, reads accessible profiles and saves campaign matches. No outreach is sent.</p>
</section>"""

SEARCH_SCRIPT = """<script>
const searchForm=document.getElementById('searchForm'),searchButton=document.getElementById('searchButton'),searchResult=document.getElementById('searchResult'),searchCampaign=document.getElementById('searchCampaign'),workerStatus=document.getElementById('workerStatus');let campaignOptions=[],lastJob=null,submitting=false;
function showSearchJob(job){if(!job)return;lastJob=job.id;const result=job.result||{};if(job.status==='queued')searchResult.textContent='Search queued. It will start when your browser worker is available.';else if(job.status==='running')searchResult.textContent='Searching LinkedIn and reading profiles. Please leave the worker browser open.';else if(job.status==='completed')searchResult.textContent=`Completed: ${result.observed||0} profiles read, ${result.accepted||0} accepted, ${result.rejected||0} rejected. ${result.message||''}`;else searchResult.textContent=result.message||'Search stopped. Check browser access and retry.';}
async function refreshSearch(){try{const r=await fetch('/api/search/settings');if(!r.ok)throw Error('Cannot load search settings');const state=await r.json();const selected=searchCampaign.value;if(JSON.stringify(state.campaigns)!==JSON.stringify(campaignOptions)){campaignOptions=state.campaigns;searchCampaign.replaceChildren(new Option('Choose campaign',''));for(const item of campaignOptions)searchCampaign.add(new Option(item.name,item.id));searchCampaign.value=selected;}workerStatus.textContent=state.mode==='local'?'Uses your saved LinkedIn login on this computer.':state.worker_online?'Browser worker online — ready to run searches.':'Browser worker offline. Searches will wait until it is started on your computer.';const active=(state.jobs||[]).some(j=>['queued','running'].includes(j.status));searchButton.disabled=submitting||active||!campaignOptions.length;if(state.jobs?.length)showSearchJob(state.jobs[0]);if(!active)await Promise.all([load(),loadSummary()]);}catch(error){workerStatus.textContent=error.message;}}
searchCampaign.onchange=()=>{const item=campaignOptions.find(x=>x.id===searchCampaign.value);document.getElementById('searchCriteria').textContent=item?.criteria||'Campaign rules determine which profiles are accepted.';};
searchForm.onsubmit=async e=>{e.preventDefault();submitting=true;searchButton.disabled=true;searchResult.textContent='Starting automatic LinkedIn search…';try{const r=await fetch('/api/search/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campaign:searchCampaign.value,max_results:Number(document.getElementById('searchLimit').value),max_pages:1})});const job=await r.json();if(!r.ok)throw Error(typeof job.detail==='string'?job.detail:'Check campaign and profile limit');showSearchJob(job);}catch(error){searchResult.textContent=error.message;}finally{submitting=false;await refreshSearch();}};
refreshSearch();setInterval(refreshSearch,5000);
</script>"""
