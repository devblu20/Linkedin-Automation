"""Fixed local entry point used by the dashboard link and Windows sign-in shortcut."""

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

from linkedin_automation.infrastructure.worker_supervisor import main  # noqa: E402

main(root)
