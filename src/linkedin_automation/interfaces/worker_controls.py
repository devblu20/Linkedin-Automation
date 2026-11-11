"""Honest local-worker controls; a web server cannot wake a switched-off computer."""

# ruff: noqa: E501 - embedded dashboard asset.

WORKER_PANEL = """<div class="toolbar" style="flex-wrap:wrap;padding-top:0" id="workerControls">
<a class="btn" id="startWorker" href="bluqq-linkedin-worker://start">Start worker on this computer</a>
<button class="btn" id="workerSetup" type="button">Worker setup / always-on</button>
<button class="btn" id="refreshWorkerStatus" type="button">Refresh worker status</button>
<span class="sub" id="workerManagement" role="status">Checking automatic restartâ€¦</span>
</div>
<dialog id="workerSetupDialog" style="max-width:620px;width:90%;padding:24px;border:1px solid #d0d5dd;border-radius:12px">
<h2>Keep your browser worker available</h2>
<p>Set up the Windows helper once on the computer with your saved LinkedIn login. It starts the worker after Windows sign-in and restarts it if the worker exits.</p>
<p><strong>Your computer must be awake and connected.</strong> A dashboard button cannot wake a switched-off or sleeping PC. A browser opens when a search or authorized delivery needs it; it need not stay open while idle.</p>
<p>The Start worker button starts the installed helper on this computer. If Windows asks, choose Open. The dashboard shows separately when LinkedIn needs verification. If no app is installed, run the one-time setup from your LinkedIn project folder:</p>
<pre style="white-space:pre-wrap">.\\scripts\\install-worker-autostart.ps1</pre>
<p class="sub">To disable automatic start at Windows sign-in, run the same setup command with <code>-DisableAutoStart</code>. This does not stop current work. No search or delivery is created by the Start worker button.</p>
<button class="btn" id="closeWorkerSetup" type="button">Close</button>
</dialog>"""

WORKER_SCRIPT = """<script>
document.addEventListener('DOMContentLoaded',()=>{
const dialog=document.getElementById('workerSetupDialog'),message=document.getElementById('workerManagement'),start=document.getElementById('startWorker');
let state=null,startingUntil=0;
document.getElementById('workerSetup').onclick=()=>dialog.showModal();
document.getElementById('closeWorkerSetup').onclick=()=>dialog.close();
document.getElementById('refreshWorkerStatus').onclick=()=>refreshSearch();
start.onclick=event=>{
if(!state){event.preventDefault();message.textContent='Checking worker settings. Please click Start again after the status loads.';refreshSearch();return;}
if(state.device_required&&!state.device_paired)return;
if(state.worker_linkedin_paused){event.preventDefault();message.textContent='Worker is running. LinkedIn needs sign-in or verification before jobs can resume.';return;}
if(state.worker_online){event.preventDefault();message.textContent='Worker is already online. You can use Search and fetch.';return;}
if(startingUntil>Date.now()){event.preventDefault();return;}
startingUntil=Date.now()+45000;
message.textContent='Starting the worker. Choose Open if Windows asks.';
setTimeout(refreshSearch,1500);
};
window.renderWorkerManagement=value=>{
state=value;
document.getElementById('workerControls').hidden=state.mode==='local';
start.href=state.device_paired?'bluqq-desktop-worker://start':'bluqq-linkedin-worker://start';
start.textContent=state.worker_online?'Worker online':state.worker_linkedin_paused?'Worker running':'Start worker on this computer';
document.getElementById('workerSetup').onclick=()=>dialog.showModal();
if(state.device_required&&!state.device_paired){start.href='/linkedin-automation/connect-computer';start.textContent='Connect this computer';document.getElementById('workerSetup').onclick=()=>location.assign('/linkedin-automation/connect-computer');message.textContent='This computer is not connected.';return;}
if(state.worker_linkedin_paused){startingUntil=0;message.textContent='Worker is running. LinkedIn needs sign-in or verification before jobs can resume.';return;}
if(state.worker_online){startingUntil=0;message.textContent='Worker online — ready for Search and fetch. Keep the configured worker computer awake.';return;}
if(startingUntil>Date.now())return;
if(startingUntil){startingUntil=0;message.textContent='Still waiting for the worker. Try Start worker again and choose Open if prompted.';return;}
message.textContent='Worker offline. Click Start worker and allow the browser to open the installed helper.';
};
refreshSearch();
});
</script>"""
