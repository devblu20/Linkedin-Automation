"""Delivery preview and explicit activation of the named, approved outreach batch."""

# ruff: noqa: E501 - embedded dashboard assets.

DELIVERY_SCRIPT = r"""<script>
document.addEventListener('DOMContentLoaded',()=>{
const view=document.getElementById('outreachView');
view.insertAdjacentHTML('beforeend','<div class="view-heading"><h2>Automatic outreach queue</h2><p id="automationMode">Checking automation…</p></div><div class="toolbar"><button type="button" class="btn" id="prepareDelivery">Prepare approved leads</button><button type="button" class="btn" id="refreshDelivery">Refresh delivery</button><span id="deliveryStatus" class="sub" role="status"></span></div><div class="table-wrap"><table><thead><tr><th>Recipient</th><th>Next action</th><th>Delivery status</th><th>Result</th><th>Controls</th></tr></thead><tbody id="deliveryRows"></tbody></table></div>');
document.body.insertAdjacentHTML('beforeend','<dialog id="deliveryDialog"><form id="deliveryForm"><div class="modal-head"><div><h2 id="deliveryRecipient">Enable delivery</h2><p class="sub" id="deliverySender"></p></div><button type="button" class="close" id="closeDelivery" aria-label="Close delivery preview">x</button></div><div class="form-grid"><div class="field full"><strong id="deliveryKind"></strong><div class="profile-value" id="deliveryDraft"></div></div><div class="field full" id="followUpPreview"><strong>Follow-up after connection acceptance</strong><div class="profile-value" id="deliveryFollowUp"></div><label><input type="checkbox" id="allowFollowUp" style="width:auto"> Also enable this exact follow-up after first-degree connection acceptance is confirmed.</label></div><div class="field full" id="reviewReplyRow" hidden><label><input type="checkbox" id="reviewReply" style="width:auto"> I reviewed the current reply and approve this one response. Future automatic follow-ups stay paused.</label></div><div class="field full"><label><input type="checkbox" id="confirmDelivery" required style="width:auto"> <span id="deliveryConsentText">I authorize sending the displayed draft to this named recipient.</span></label><p class="sub">Daily campaign limits apply. Unconfirmed deliveries stop for manual inspection and are never blindly resent.</p></div></div><div class="field full" id="resolveFields" hidden style="padding:0 22px"><label for="resolveNote">What did you check on LinkedIn?</label><textarea id="resolveNote" minlength="10" maxlength="1000"></textarea></div><div class="modal-actions"><span id="authorizeResult" class="sub" role="status"></span><button type="submit" class="btn primary" id="authorizeDelivery">Enable delivery</button></div></form></dialog>');
const el=id=>document.getElementById(id);
const notNow=document.createElement('button');
notNow.type='button';
notNow.className='btn';
notNow.textContent='Not now';
notNow.onclick=()=>el('deliveryDialog').close();
el('authorizeDelivery').before(notNow);
let current=null,serial=0,resolving=false,cloud=false,sendingAvailable=true,browserSender='';
let localSignInNeeded=false;
const deliverySignInURL='bluqq-linkedin-delivery://open';
function signInLink(){
 const link=document.createElement('a');
 link.className='icon-btn';
 link.href=deliverySignInURL;
 link.textContent='Open LinkedIn sign-in';
 return link;
}
function cell(row,value){const td=document.createElement('td');td.textContent=value??'';row.append(td);return td}
function control(text,action){const b=document.createElement('button');b.type='button';b.className='icon-btn';b.textContent=text;b.onclick=action;return b}
async function post(path,payload={}){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),body=await r.json();if(!r.ok)throw Error(typeof body.detail==='string'?body.detail:'Could not update delivery');return body}
function updateConfirmButton(){
 const button=el('authorizeDelivery');
 if(resolving){
  button.textContent='Confirm not sent';
 }else if(current.action==='connection'){
  button.textContent=el('allowFollowUp').checked?'Confirm request and follow-up':'Confirm connection request';
 }else{
  button.textContent='Confirm and send message';
 }
}
function preview(job,resolve=false){
 const sender=el('deliverySender');
 sender.textContent=cloud?'Uses your connected LinkedIn cloud account.':(browserSender?'Uses approved local LinkedIn sender: '+browserSender:'Uses the LinkedIn account signed in on the local worker computer.');
 current=job;
 resolving=resolve;
 el('deliveryForm').reset();
 const needsReplyReview=!resolve&&job.action==='message'&&job.followup_hold==='Recipient replied';
 el('reviewReplyRow').hidden=!needsReplyReview;
 el('reviewReply').required=needsReplyReview;
 el('resolveFields').hidden=!resolve;
 el('resolveNote').required=resolve;
 const confirmation=el('confirmDelivery');
 confirmation.closest('label').hidden=!resolve;
 confirmation.required=resolve;
 el('deliveryConsentText').textContent='I checked LinkedIn and confirm this invitation/message was NOT sent.';
 el('authorizeResult').textContent='';
 el('deliveryRecipient').textContent=(resolve?'Resolve unconfirmed delivery: ':'Send to ')+job.payload.name;
 el('deliveryKind').textContent=job.action==='connection'?'Connection request note':'Follow-up message';
 el('deliveryDraft').textContent=job.payload.draft;
 el('deliveryFollowUp').textContent=job.payload.follow_up;
 const hasFollowUp=!resolve&&job.action==='connection'&&Boolean(job.payload.follow_up?.trim());
 el('followUpPreview').hidden=!hasFollowUp;
 el('allowFollowUp').checked=hasFollowUp;
 updateConfirmButton();
 el('deliveryDialog').showModal();
}
el('allowFollowUp').onchange=updateConfirmButton;
async function refreshDelivery(){const id=++serial;try{const r=await fetch('/api/outreach/jobs');if(!r.ok)throw Error('Could not load delivery queue');const data=await r.json();if(id!==serial)return;cloud=data.execution==='cloud';browserSender=data.browser_sender||'';sendingAvailable=!cloud||data.cloud_sending_enabled;localSignInNeeded=data.local_sign_in_available&&data.local_worker_online===false;el('automationMode').textContent='Eligible leads are prepared automatically when you open Outreach. Review the recipient and drafts to confirm delivery.';const rows=el('deliveryRows');rows.replaceChildren();for(const job of data.jobs){const row=document.createElement('tr');cell(row,job.payload.name);cell(row,{connection:'Connection request',check:'Check acceptance',message:'Follow-up message'}[job.action]);cell(row,job.followup_hold&&job.status==='queued'?'Follow-up paused: '+job.followup_hold:job.status==='queued'&&!job.authorized?'Awaiting delivery authorization':job.status.replaceAll('_',' '));cell(row,job.result?.proof||'');const actions=cell(row,'');if(job.status==='queued'){if(sendingAvailable&&!job.followup_hold&&!job.authorized&&job.action!=='check')actions.append(control('Review / enable delivery',()=>preview(job)));if(localSignInNeeded&&job.authorized&&!job.followup_hold&&job.action!=='check')actions.append(signInLink());if(job.action==='check')actions.append(control('Check acceptance now',async()=>{try{await post('/api/outreach/jobs/'+job.id+'/check-now');await refreshDelivery()}catch(error){el('deliveryStatus').textContent=error.message}}));actions.append(control('Cancel unstarted action',async()=>{try{await post('/api/outreach/jobs/'+job.id+'/cancel');await Promise.all([refreshDelivery(),load(),loadSummary()])}catch(error){el('deliveryStatus').textContent=error.message}}))}if(job.status==='uncertain'||job.status==='failed'){if(job.action==='check')actions.append(control('Retry acceptance check',async()=>{try{await post('/api/outreach/jobs/'+job.id+'/check-now');await refreshDelivery()}catch(error){el('deliveryStatus').textContent=error.message}}));else{actions.append(control('Verify / record actual outcome',()=>window.openManualActivity(job.lead_id)));actions.append(control('Confirm not sent',()=>preview(job,true)))}}rows.append(row)}el('deliveryStatus').textContent=data.jobs.length?data.jobs.length+' delivery records':'No delivery jobs yet. Only approved, qualifying leads can be prepared.';}catch(error){el('deliveryStatus').textContent=error.message}}
el('prepareDelivery').onclick=async e=>{e.currentTarget.disabled=true;try{const data=await post('/api/outreach/prepare');notice(data.prepared+' approved leads prepared. No message has been sent.');await Promise.all([refreshDelivery(),load(),loadSummary()])}catch(error){el('deliveryStatus').textContent=error.message}finally{el('prepareDelivery').disabled=false}};
el('refreshDelivery').onclick=()=>Promise.all([refreshDelivery(),load(),loadSummary()]);el('closeDelivery').onclick=()=>el('deliveryDialog').close();
el('deliveryForm').onsubmit=async e=>{
 e.preventDefault();
 const button=el('authorizeDelivery');
 const openSignIn=!resolving&&localSignInNeeded;
 button.disabled=true;
 try{
  if(resolving){
   await post('/api/outreach/jobs/'+current.id+'/resolve-not-sent',{confirmed:el('confirmDelivery').checked,note:el('resolveNote').value});
  }else{
   await post('/api/outreach/jobs/'+current.id+'/authorize',{draft_hash:current.draft_hash,confirmed:true,include_follow_up:el('allowFollowUp').checked,reviewed_reply:el('reviewReply').checked});
  }
  el('deliveryDialog').close();
  if(resolving){
   notice('Outcome recorded as not sent. Prepare again only after verifying LinkedIn.');
  }else if(openSignIn){
   notice('Delivery queued. Allow Windows to open the helper, or use Open LinkedIn sign-in on the queued row.');
   const link=signInLink();
   link.hidden=true;
   view.append(link);
   link.click();
   link.remove();
  }else{
   notice(cloud?'Cloud delivery enabled for the named recipient. Provider confirmation will update the status.':'Delivery enabled. If LinkedIn needs sign-in, complete it in the worker Chrome window.');
  }
  await Promise.all([refreshDelivery(),load(),loadSummary()]);
 }catch(error){
  el('authorizeResult').textContent=error.message;
 }finally{
  button.disabled=false;
 }
};
let preparingConfirmation=false;
async function prepareForConfirmation(){
 if(location.hash!=='#outreach'||preparingConfirmation)return;
 preparingConfirmation=true;
 try{
  const {prepared}=await post('/api/outreach/prepare?guided=true');
  if(prepared)await Promise.all([load(),loadSummary()]);
  await refreshDelivery();
 }catch(error){
  el('deliveryStatus').textContent=error.message;
 }finally{
  preparingConfirmation=false;
 }
}
window.addEventListener('hashchange',prepareForConfirmation);
el('refreshDelivery').addEventListener('click',prepareForConfirmation);
if(location.hash==='#outreach')prepareForConfirmation();
else refreshDelivery();
setInterval(refreshDelivery,15000);
});
</script>"""
