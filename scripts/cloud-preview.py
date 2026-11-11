"""Preview the new cloud dashboard locally without changing production mode."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root / "src"))
for filename in (".env.railway.local", ".env.auth.local", ".env.unipile.local"):
    for name, value in dotenv_values(root / filename).items():
        if value is not None and name.startswith(
            ("DASHBOARD_", "AUTH_", "GOOGLE_LOGIN_", "UNIPILE_", "CLOUD_CALLBACK_")
        ):
            os.environ[name] = value
port = int(os.getenv("CLOUD_PREVIEW_PORT", "8775"))
os.environ.update(
    WEBSITE_CLOUD_MODE="1",
    WEBSITE_AUTH_MODE="1",
    CLOUD_ACCOUNT_MODE="0",
    HOSTED_DATA_DIR=str(root / "data/cloud-preview"),
    AUTH_PUBLIC_ORIGIN=f"http://127.0.0.1:{port}",
)
os.environ.pop("DASHBOARD_PUBLIC_ORIGIN", None)
import uvicorn  # noqa: E402

from linkedin_automation.interfaces.access_gateway import create_access_gateway  # noqa: E402
from linkedin_automation.interfaces.hosted_site import create_hosted_app  # noqa: E402

print(f"Cloud preview: http://127.0.0.1:{port}/linkedin-automation/admin", flush=True)
print(
    "Uses separate preview data. Production remains unchanged. "
    "Use the existing admin login for preview.",
    flush=True,
)
uvicorn.run(create_access_gateway(create_hosted_app()), host="127.0.0.1", port=port)
