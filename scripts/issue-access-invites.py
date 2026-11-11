"""Owner-only signup invitations. Writes secrets to a private file, never stdout."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from linkedin_automation.infrastructure.access_auth import AccessSettings, AccessStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--email", action="append", default=[])
    args = parser.parse_args()
    for key, value in dotenv_values(root / ".env.auth.local").items():
        if value is not None and key.startswith("AUTH_"):
            os.environ[key] = value
    settings = AccessSettings.from_environment()
    emails = args.email or sorted(settings.allowed_emails)
    if not emails or any(not settings.permits(email) for email in emails):
        raise ValueError("Only explicitly allowlisted emails may be invited")
    store = AccessStore(args.data_root.resolve() / "website-auth")
    output = args.output.resolve()
    # Refuse to overwrite a file so existing invitation material is not lost.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        output.chmod(0o600)
        issued = {email: store.invite(email) for email in emails}
        json.dump(
            {
                "issued_at": datetime.now(UTC).isoformat(),
                "valid_for_days": 7,
                "signup_codes": issued,
            },
            stream,
            indent=2,
        )
    print(
        f"Invitations issued for {len(issued)} approved email(s). "
        f"Codes saved privately to {output}."
    )
    print("Share each code only with its intended user. No email was sent.")


if __name__ == "__main__":
    main()
