"""Local auth preview with separate data and no inherited production credentials."""

import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / "src"))
    for name, value in dotenv_values(root / ".env.auth.local").items():
        if value is not None and name.startswith(("AUTH_", "GOOGLE_LOGIN_")):
            os.environ[name] = value
    port = int(os.getenv("AUTH_PREVIEW_PORT", "8773"))
    os.environ.update(
        {
            "HOSTED_DATA_DIR": str(root / "data" / "auth-preview"),
            "DASHBOARD_USERNAME": "local-preview-internal",
            "DASHBOARD_PASSWORD": secrets.token_urlsafe(48),
            "WEBSITE_AUTH_MODE": "1",
            "CLOUD_ACCOUNT_MODE": "0",
            "AUTH_PUBLIC_ORIGIN": f"http://127.0.0.1:{port}",
        }
    )
    os.environ.pop("DASHBOARD_PUBLIC_ORIGIN", None)
    import uvicorn

    from linkedin_automation.interfaces.access_gateway import create_access_gateway
    from linkedin_automation.interfaces.hosted_site import create_hosted_app

    print(f"Preview: http://127.0.0.1:{port}/linkedin-automation/login", flush=True)
    print("Uses separate local preview data. Live services are unchanged.", flush=True)
    uvicorn.run(create_access_gateway(create_hosted_app()), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
