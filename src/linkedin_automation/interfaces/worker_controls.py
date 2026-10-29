"""Honest local-worker controls; a web server cannot wake a switched-off computer."""

# ruff: noqa: E501 - embedded dashboard asset.

WORKER_PANEL = """<div class="toolbar" style="flex-wrap:wrap;padding-top:0" id="workerControls">
<a class="btn" id="startWorker" href="bluqq-linkedin-worker://start">Start worker on this computer</a>
<button class="btn" id="workerSetup" type="button">Worker setup / always-on</button>
<button class="btn" id="refreshWorkerStatus" type="button">Refresh worker status</button>
<span class="sub" id="workerManagement" role="status">Checking automatic restart…</span>
</div>
<dialog id="workerSetupDialog" style="max-width:620px;width:90%;padding:24px;border:1px solid #d0d5dd;border-radius:12px">
<h2>Keep your browser worker available</h2>
<p>Set up the Windows helper once on the computer with your saved LinkedIn login. It starts the worker after Windows sign-in and restarts it if the worker exits.</p>
<p><strong>Your computer must be awake and connected.</strong> A dashboard button cannot wake a switched-off or sleeping PC. A browser opens when a search or authorized delivery needs it; it need not stay open while idle.</p>
<p>The Start worker button opens the installed helper on this computer. If Windows asks, choose Open. If no app is installed, run the one-time setup from your LinkedIn project folder:</p>
<pre style="white-space:pre-wrap">.\\scripts\\install-worker-autostart.ps1</pre>
<p class="sub">To disable automatic start at Windows sign-in, run the same setup command with <code>-DisableAutoStart</code>. This does not stop current work. No search or delivery is created by the Start worker button.</p>
<button class="btn" id="closeWorkerSetup" type="button">Close</button>
</dialog>"""

WORKER_SCRIPT = """<script>
document.addEventListener('DOMContentLoaded',()=>{
const dialog=document.getElementById('workerSetupDialog'),message=document.getElementById('workerManagement');
document.getElementById('workerSetup').onclick=()=>dialog.showModal();
document.getElementById('closeWorkerSetup').onclick=()=>dialog.close();
document.getElementById('refreshWorkerStatus').onclick=()=>refreshSearch();
document.getElementById('startWorker').onclick=()=>{message.textContent='Start requested on this computer. Accept the Windows Open prompt if shown. Waiting for the worker heartbeat…';setTimeout(refreshSearch,1500)};
window.renderWorkerManagement=state=>{document.getElementById('workerControls').hidden=state.mode==='local';message.textContent=state.worker_online&&state.worker_managed?(state.worker_autostart?'Automatic restart on · Starts at Windows sign-in':'Automatic restart on · Windows auto-start is not enabled'):state.worker_online?'Worker online · Open Worker setup to enable automatic restart/startup':'Use Start worker on the configured Windows computer. Keep it awake; no worker can run while it is shut down.';};
refreshSearch();
});
</script>"""
