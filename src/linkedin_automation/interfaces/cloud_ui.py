"""No-install onboarding and account controls for private cloud workspaces."""

# ruff: noqa: E501 - embedded onboarding HTML and JavaScript.

import html


def welcome(message: str = "", *, signed_in=False):
    action = (
        '<button id="connect">Connect LinkedIn</button><p>Account connection is handled by Unipile. '
        "You will sign in and complete LinkedIn verification on its hosted page. "
        "BluQQ does not collect your LinkedIn password.</p>"
        if signed_in
        else '<a class="button" href="/linkedin-automation/api/cloud/login">Continue with LinkedIn</a>'
        "<p>Sign in with LinkedIn to open your private workspace. Then connect your LinkedIn "
        "account for cloud searches and authorized outreach.</p>"
    )
    return (
        """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BluQQ — Connect LinkedIn</title><style>
body{margin:0;background:#f4f6fa;color:#17263d;font:16px/1.6 system-ui,sans-serif}
main{max-width:580px;margin:8vh auto;padding:38px;background:white;border:1px solid #dce3ed;border-radius:18px}
h1{font-size:30px;line-height:1.2}p{color:#52647e}.button,button{display:inline-block;border:0;border-radius:8px;background:#315dff;color:white;padding:13px 22px;text-decoration:none;font:inherit;cursor:pointer}
.note{padding:12px;background:#eff5ff;border-radius:8px}small{color:#52647e}@media(max-width:650px){main{margin:20px;padding:24px}}
</style></head><body><main><strong>BluQQ · LinkedIn Automation</strong><h1>Your LinkedIn workflow, in one place.</h1>
<p>No worker, extension or software installation is needed on your computer. Your campaigns, leads and connection are private to your workspace.</p>
"""
        + action
        + '<p id="result" class="note">'
        + html.escape(message)
        + """</p>
<small>Connection requests and messages require your review and authorization. LinkedIn may request verification or reconnection.</small>
<script>const connect=document.getElementById('connect');if(connect)connect.onclick=async()=>{connect.disabled=true;try{const r=await fetch('/linkedin-automation/api/cloud/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),data=await r.json();if(!r.ok)throw Error(data.detail||'Connection is unavailable');window.location.assign(data.url)}catch(e){document.getElementById('result').textContent=e.message;connect.disabled=false}};
"""
        + (
            """setInterval(async()=>{try{const r=await fetch('/linkedin-automation/api/cloud/account'),s=await r.json();if(s.connected)window.location.replace('/linkedin-automation/')}catch{}},3000);"""
            if signed_in
            else ""
        )
        + "</script></main></body></html>"
    )


ACCOUNT_PANEL = """<section class="panel" style="padding:16px 20px;margin-bottom:16px"><strong>Private LinkedIn cloud connection</strong><p id="cloudAccountState">Checking your connection…</p><button class="btn" type="button" id="reconnectCloud">Reconnect LinkedIn</button> <button class="btn" type="button" id="logoutCloud">Sign out</button></section>"""

ACCOUNT_SCRIPT = """<script>
document.addEventListener('DOMContentLoaded',()=>{
async function account(){const r=await fetch('/api/cloud/account'),v=await r.json();document.getElementById('cloudAccountState').textContent=v.connected?'Connected for '+v.name+'. Runs on the server; your computer does not need a worker.':'Your LinkedIn connection needs attention. Reconnect to resume.';}
document.getElementById('reconnectCloud').onclick=async()=>{const r=await fetch('/api/cloud/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),v=await r.json();if(r.ok)window.location.assign(v.url);else notice(v.detail||'Could not connect');};
document.getElementById('logoutCloud').onclick=async()=>{await fetch('/api/cloud/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});window.location.reload()};
account();setInterval(account,30000);
});</script>"""
