"""Standalone Windows research helper. No owner credentials or LinkedIn cookies ship."""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from threading import Event, Thread
from urllib.request import Request, urlopen

ORIGIN = "https://linkedin-automation-production-7964.up.railway.app"
PUBLIC = "https://www.bluqq.com/linkedin-automation"
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BluQQWorker"


def own_process_tree():
    """Windows closes this helper's workers when the helper is closed or crashes."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("min_working", ctypes.c_size_t),
            ("max_working", ctypes.c_size_t),
            ("processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (
        not handle
        or not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())
    ):
        raise OSError("Cannot supervise local workers safely")
    return handle


def api(path, payload):
    request = Request(
        ORIGIN + "/api/auth/devices/" + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read(8192))


def configure(profile):
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    database = ROOT / profile / "research.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "LINKEDIN_AUTOMATION_DATABASE": str(database),
            "LINKEDIN_AUTOMATION_DATABASE_URL": "sqlite:///" + database.as_posix(),
            "LINKEDIN_AUTOMATION_BROWSER_DATA": str(database.parent / "browser"),
            "LINKEDIN_AUTOMATION_ARTIFACT_DIR": str(database.parent / "artifacts"),
            "LINKEDIN_AUTOMATION_SCREENSHOT_DIR": str(database.parent / "screenshots"),
            "LINKEDIN_AUTOMATION_BROWSER_CHANNEL": "chrome",
            "LINKEDIN_AUTOMATION_ENABLE_OUTREACH_WORKER": "0",
            "LINKEDIN_AUTOMATION_WORKER_MANAGED": "1",
            "LINKEDIN_AUTOMATION_WORKER_AUTOSTART": "1",
        }
    )


def install():
    import winreg

    ROOT.mkdir(parents=True, exist_ok=True)
    destination = ROOT / "BluQQ-Worker.exe"
    portable = (RESOURCES / "runtime/python.exe").is_file()
    if portable:
        target = ROOT / "app"
        if RESOURCES.resolve() != target.resolve():
            for folder in ("runtime", "lib", "src", "config", "scripts"):
                shutil.copytree(RESOURCES / folder, target / folder, dirs_exist_ok=True)
        command = (
            '"'
            + str(target / "runtime/python.exe")
            + '" "'
            + str(target / "scripts/desktop-worker.py")
            + '"'
            + ' --start-existing'
        )
    elif getattr(sys, "frozen", False) and Path(sys.executable).resolve() != destination.resolve():
        if (
            not destination.exists()
            or hashlib.sha256(destination.read_bytes()).digest()
            != hashlib.sha256(Path(sys.executable).read_bytes()).digest()
        ):
            shutil.copy2(sys.executable, destination)
    else:
        destination = Path(sys.executable)
    if not getattr(sys, "frozen", False) and not portable:
        return
    if not portable:
        command = '"' + str(destination) + '" --start-existing'
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Classes\bluqq-desktop-worker"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:BluQQ Desktop Worker")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Classes\bluqq-desktop-worker\shell\open\command"
    ) as key:
        # URL arguments are deliberately not forwarded to the executable.
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
    ) as key:
        winreg.SetValueEx(key, "BluQQDesktopWorker", 0, winreg.REG_SZ, command)


def worker(config):
    from linkedin_automation.infrastructure.search_worker import DashboardClient, run_worker

    configure(config["id"])
    client = DashboardClient(ORIGIN, config["username"], config["token"])
    run_worker(client, RESOURCES / "config")


def self_test():
    from playwright.sync_api import sync_playwright

    from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
    from linkedin_automation.infrastructure.search_worker import configured_campaigns

    configure("self-test")
    repository = DatabaseResearchRepository(os.environ["LINKEDIN_AUTOMATION_DATABASE_URL"])
    repository.initialize()
    repository.close()
    assert len(configured_campaigns(RESOURCES / "config")) >= 3
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.set_content("<title>BluQQ helper test</title>")
        assert page.title() == "BluQQ helper test"
        browser.close()
    print("SELF_TEST_OK: bundled campaigns, database and Chrome")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    if "--worker" in sys.argv:
        identifier = sys.argv[sys.argv.index("--worker") + 1]
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid local device identifier")
        config_file = ROOT / "devices" / (identifier + ".json")
        worker(json.loads(config_file.read_text(encoding="utf-8")))
        return
    from linkedin_automation.infrastructure.worker_supervisor import supervisor_lock

    with supervisor_lock(ROOT / "helper.lock") as acquired:
        if not acquired:
            if "--start-existing" not in sys.argv:
                (ROOT / "pair-again").touch()
            print("Existing helper is already running.")
            return
        install()
        job_handle = own_process_tree()
        assert job_handle
        devices = ROOT / "devices"
        devices.mkdir(exist_ok=True)
        processes = {}
        reconnect = Event()

        def keyboard():
            while True:
                try:
                    input()
                    reconnect.set()
                except (EOFError, OSError):
                    return

        Thread(target=keyboard, daemon=True).start()
        print(
            "BluQQ LinkedIn helper · This computer only\n"
            "Keep this window running. Close it to stop.",
            flush=True,
        )

        def pair():
            token = secrets.token_urlsafe(48)
            label = (
                re.sub(r"[^\w .()-]", "", os.environ.get("COMPUTERNAME", "Windows PC"))[:80]
                or "Windows PC"
            )
            pairing = api(
                "begin", {"token_hash": hashlib.sha256(token.encode()).hexdigest(), "label": label}
            )
            print(
                "\nComputer: "
                + label
                + "\nPairing code: "
                + pairing["code"]
                + "\nOpen the page below, sign in, check the computer name and connect:\n"
                + PUBLIC
                + "/connect-computer",
                flush=True,
            )
            webbrowser.open(PUBLIC + "/connect-computer?code=" + pairing["code"])
            deadline = time.monotonic() + pairing["expires_in"]
            while time.monotonic() < deadline:
                result = api("poll", {"id": pairing["id"], "token": token})
                if result["status"] == "paired":
                    config = {**result, "id": pairing["id"], "token": token}
                    (devices / (pairing["id"] + ".json")).write_text(
                        json.dumps(config), encoding="utf-8"
                    )
                    print(
                        "Connected: " + config["email"] + ". Return to the dashboard.", flush=True
                    )
                    return
                time.sleep(3)
            else:
                raise ValueError("Pairing expired. Close this window and open the helper again.")

        if not list(devices.glob("*.json")):
            if "--start-existing" in sys.argv:
                print("No configured workspace worker is installed on this PC.", flush=True)
                return
            reconnect.set()
        print(
            "Press Enter to connect another browser or approved account.\n"
            "LinkedIn login is requested in a separate local browser when searching.",
            flush=True,
        )
        while True:
            if (ROOT / "pair-again").exists():
                (ROOT / "pair-again").unlink()
                reconnect.set()
            if reconnect.is_set():
                reconnect.clear()
                try:
                    pair()
                except Exception as error:
                    print(
                        "Pairing paused: " + type(error).__name__ + ". Press Enter to retry.",
                        flush=True,
                    )
            for path in devices.glob("*.json"):
                identifier = path.stem
                if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                    continue
                if identifier in processes and processes[identifier].poll() is None:
                    continue
                command = (
                    [sys.executable]
                    if getattr(sys, "frozen", False)
                    else [sys.executable, str(Path(__file__).resolve())]
                )
                with (ROOT / (identifier + ".log")).open("ab") as log:
                    processes[identifier] = subprocess.Popen(
                        [*command, "--worker", identifier],
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not expose tokens, HTTP bodies or credential-bearing exception URLs.
        print(
            "Helper stopped: "
            + type(error).__name__
            + ". Check internet access and reopen the helper.",
            flush=True,
        )
        if "--worker" not in sys.argv and "--self-test" not in sys.argv:
            input("Press Enter to close.")
        raise SystemExit(1) from None
