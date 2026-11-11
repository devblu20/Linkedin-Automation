"""Add source/newest filters to existing saved review rows without changing workflow."""

# ruff: noqa: E501 - embedded dashboard asset.
SCRIPT = r"""<script>
(()=>{
const search=document.getElementById('search'),toolbar=search.closest('.toolbar');
const source=document.createElement('select');source.id='leadSourceFilter';source.setAttribute('aria-label','Lead research source');
for(const [value,text] of [['all','All research sources'],['brave','Brave public web'],['other','Other research']])source.add(new Option(text,value));
const order=document.createElement('select');order.id='leadReviewSort';order.setAttribute('aria-label','Saved lead order');
for(const [value,text] of [['newest','Newest collected first'],['score','Highest score first'],['name','Name A-Z']])order.add(new Option(text,value));
const brave=document.createElement('button');brave.id='reviewBraveLeads';brave.type='button';brave.className='btn';brave.textContent='Review Brave profiles';
const refresh=document.createElement('button');refresh.id='refreshSavedLeads';refresh.type='button';refresh.className='btn';refresh.textContent='Refresh leads';
toolbar.append(source,order,brave,refresh);
const summary=document.createElement('p');summary.id='savedReviewSummary';summary.className='view-status';summary.style.padding='0 19px';summary.setAttribute('role','status');toolbar.after(summary);
const isBrave=item=>(item.lead.source_search||'').startsWith('Brave public web index:');
const baseRender=render;
render=function(){
 const all=leads,braveCount=all.filter(isBrave).length;
 const selected=all.filter(item=>source.value==='all'||(source.value==='brave'?isBrave(item):!isBrave(item)));
 const date=item=>Date.parse(item.lead.last_observed_at)||0;
 selected.sort((a,b)=>order.value==='score'?b.outreach.score-a.outreach.score||date(b)-date(a):order.value==='name'?a.lead.full_name.localeCompare(b.lead.full_name):date(b)-date(a)||a.lead.full_name.localeCompare(b.lead.full_name));
 const query=search.value.toLowerCase(),status=document.getElementById('statusFilter').value;
 const visible=selected.filter(item=>{const l=item.lead;return (!query||[l.full_name,l.company,l.current_title,l.headline,l.location].join(' ').toLowerCase().includes(query))&&(!status||item.outreach.status===status);});
 try{leads=selected;baseRender();}finally{leads=all;}
 for(const [index,row] of [...document.getElementById('leadRows').children].entries()){
  const item=visible[index];if(!item||!isBrave(item))continue;
  const badge=document.createElement('small');badge.textContent='Brave public web · evidence requires review';badge.style.display='block';row.querySelector('.person > div:last-child')?.append(badge);
 }
 summary.textContent=`Showing ${visible.length} of ${all.length} saved leads in this workspace · ${braveCount} from Brave. Every Brave prospect has a qualification score and Review action; a score is not approval.`;
 brave.textContent=`Review Brave profiles (${braveCount})`;brave.disabled=!braveCount;
};
source.onchange=order.onchange=()=>render();
brave.onclick=()=>{source.value='brave';order.value='newest';search.value='';document.getElementById('statusFilter').value='';render();document.getElementById('leadRows').scrollIntoView({behavior:'smooth',block:'start'});};
refresh.onclick=async()=>{refresh.disabled=true;try{await Promise.all([load(),loadSummary()]);}catch(error){notice('Could not refresh saved leads. Please try again.');}finally{refresh.disabled=false;}};
render();setInterval(()=>{if(document.visibilityState==='visible'&&!document.getElementById('editor').open)Promise.all([load(),loadSummary()]).catch(()=>{});},30000);
})();
</script>"""
