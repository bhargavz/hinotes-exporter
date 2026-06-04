#!/usr/bin/env python3
"""
hinotes_export.py — Export notes from hinotes.hidock.com to local Markdown files.

Usage:
    python hinotes_export.py --output ~/Downloads/hinotes
    python hinotes_export.py --output ~/Downloads/hinotes --folder 1on1s
    python hinotes_export.py --output ~/Downloads/hinotes --force   # re-download all

First run opens a browser for you to log in (and solve the CAPTCHA).
The access token is saved to ~/.hinotes_exporter/session.json and reused
on subsequent runs until it expires.
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

CONFIG_DIR = Path.home() / ".hinotes_exporter"
SESSION_FILE = CONFIG_DIR / "session.json"
BASE = "https://hinotes.hidock.com"

_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Interface-Language": "en",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    """Strip filesystem-unsafe characters and trim length."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
    return name[:200] or "unnamed"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _load_token() -> str | None:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text()).get("accesstoken")
        except Exception:
            pass
    return None


def _save_token(token: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"accesstoken": token}, indent=2))
    SESSION_FILE.chmod(0o600)


def _validate_token(token: str) -> bool:
    try:
        r = requests.post(
            f"{BASE}/v1/user/info",
            json={},
            headers={**_HEADERS, "accesstoken": token, "Content-Type": "application/json"},
            timeout=10,
        )
        data = r.json()
        if data.get("error") == 0:
            user = data["data"]
            print(f"   Signed in as {user['name']} ({user['email']})")
            return True
        return False
    except Exception:
        return False


def _browser_login() -> str:
    """Open a Chromium window so the user can log in and solve the CAPTCHA.
    The access token is captured from the /v1/user/signin network response."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright is not installed.\n"
            "Fix: pip install playwright && playwright install chromium"
        )
        sys.exit(1)

    token_box: list[str | None] = [None]

    def on_response(resp):
        if "/v1/user/signin" in resp.url and resp.status == 200:
            try:
                data = resp.json()
                if data.get("error") == 0 and data.get("data"):
                    token_box[0] = data["data"]
            except Exception:
                pass

    print("\n🌐  Opening browser — please log in and solve the CAPTCHA.")
    print("    The window will be closed automatically once login succeeds.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.on("response", on_response)
        page.goto(f"{BASE}/login")

        # Wait up to 5 minutes for the user to complete login
        for _ in range(600):
            if token_box[0]:
                break
            try:
                page.wait_for_timeout(500)
            except Exception:
                break

        try:
            browser.close()
        except Exception:
            pass

    token = token_box[0]
    if not token:
        print("Login was cancelled or timed out.")
        sys.exit(1)

    _save_token(token)
    print("✅  Login successful — token saved to", SESSION_FILE)
    return token


def get_token() -> str:
    token = _load_token()
    if token:
        print("🔑  Checking saved session…")
        if _validate_token(token):
            return token
        print("   Session expired, re-login required.")
    return _browser_login()


# ── API helpers ───────────────────────────────────────────────────────────────

def _get(token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(
        f"{BASE}{path}",
        params=params,
        headers={**_HEADERS, "accesstoken": token},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _post(token: str, path: str, payload: dict | None = None) -> dict:
    r = requests.post(
        f"{BASE}{path}",
        json=payload or {},
        headers={**_HEADERS, "accesstoken": token, "Content-Type": "application/json"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


# ── HiNotes API ───────────────────────────────────────────────────────────────

def get_folders(token: str) -> list[dict]:
    """Return all user folders, each with {id, name, noteCount}."""
    data = _post(token, "/v1/folder/list")
    if data.get("error") != 0:
        raise RuntimeError(f"folder/list error: {data}")
    return data["data"]


def iter_folder_notes(token: str, folder_id: str, page_size: int = 50):
    """Yield every note metadata dict in a folder, handling pagination."""
    page = 0
    while True:
        data = _get(token, "/v1/note/recording/list", {
            "folderId": folder_id,
            "pageIndex": page,
            "pageSize": page_size,
            "sortType": "asc",       # oldest-first so files sort naturally
            "sortField": "createtime",
        })
        if data.get("error") != 0:
            raise RuntimeError(f"note list error: {data}")
        page_data = data["data"]
        yield from page_data["content"]
        if page_data["last"]:
            break
        page += 1


def export_note(token: str, note_id: str, tz_offset: int) -> tuple[str, str]:
    """Download a note as Markdown.

    Returns (server_filename, markdown_text).
    The server_filename is decoded from Content-Disposition (e.g.
    '2026-04-23 ETD Technical Sync.md').
    """
    r = requests.get(
        f"{BASE}/v2/note/export",
        params={
            "note_id": note_id,
            "scope": "summary",
            "language": "en",
            "tz_offset": tz_offset,
            "export_format": "markdown",
        },
        headers={**_HEADERS, "accesstoken": token},
        timeout=30,
    )
    r.raise_for_status()

    # Extract filename from Content-Disposition header
    cd = r.headers.get("Content-Disposition", "")
    server_name = ""
    if "filename=" in cd:
        raw = cd.split("filename=")[-1].strip().strip('"')
        server_name = urllib.parse.unquote(raw)

    # The response body is the raw Markdown text
    text = r.content.decode("utf-8", errors="replace")
    return server_name, text


# ── Manifest (skip-already-downloaded) ────────────────────────────────────────

def load_manifest(out_dir: Path) -> set[str]:
    p = out_dir / ".manifest.json"
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception:
            pass
    return set()


def save_manifest(out_dir: Path, ids: set[str]) -> None:
    (out_dir / ".manifest.json").write_text(
        json.dumps(sorted(ids), indent=2), encoding="utf-8"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export HiNotes notes to local Markdown files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--output", "-o", required=True,
        help="Directory where notes will be saved (created if absent).",
    )
    ap.add_argument(
        "--force", "-f", action="store_true",
        help="Re-download notes that were already downloaded.",
    )
    ap.add_argument(
        "--folder",
        help="Only export notes from this folder (case-insensitive name match).",
    )
    ap.add_argument(
        "--tz-offset", type=int, default=420,
        help="Timezone offset in minutes from UTC (default: 420 = UTC-7 Pacific).",
    )
    ap.add_argument(
        "--include-unfiled", action="store_true",
        help="Also export notes not assigned to any folder (saved under _unfiled/).",
    )
    ap.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds to wait between downloads to avoid rate-limiting (default: 0.3).",
    )
    args = ap.parse_args()

    out = Path(args.output).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    print(f"📥  Output directory: {out}")

    # ── Auth ──
    token = get_token()

    # ── Skip set ──
    done: set[str] = set() if args.force else load_manifest(out)
    if done:
        print(f"   {len(done)} note(s) already downloaded (use --force to re-download).")

    # ── Folders ──
    print("\n📂  Fetching folder list…")
    all_folders = get_folders(token)

    if not all_folders:
        print("No folders found. Your account might be empty.")
        sys.exit(0)

    # Pretty-print folder list
    print(f"   Found {len(all_folders)} folder(s):")
    for f in all_folders:
        print(f"   • {f['name']}  ({f['noteCount']} notes)")

    # Apply --folder filter
    if args.folder:
        folders = [f for f in all_folders if f["name"].lower() == args.folder.lower()]
        if not folders:
            print(f"\nFolder '{args.folder}' not found.")
            sys.exit(1)
    else:
        folders = list(all_folders)

    # Optional unfiled bucket
    if args.include_unfiled:
        folders.append({"id": "-1", "name": "_unfiled", "noteCount": "?"})

    # ── Download loop ──
    n_dl = n_skip = n_err = 0

    for folder in folders:
        folder_name = sanitize(folder["name"])
        folder_dir = out / folder_name
        folder_dir.mkdir(exist_ok=True)

        count_label = folder.get("noteCount", "?")
        print(f"\n📁  {folder['name']}  ({count_label} notes)")

        for note in iter_folder_notes(token, folder["id"]):
            note_id: str = note["id"]
            title: str = note.get("title") or f"note_{note_id}"

            if note_id in done:
                print(f"    ⏭  {title}")
                n_skip += 1
                continue

            try:
                server_name, md_text = export_note(token, note_id, args.tz_offset)

                # Build filename: prefer server-provided name, fall back to title
                if server_name:
                    stem = Path(server_name).stem
                else:
                    stem = sanitize(title)
                fname = f"{sanitize(stem)}.md"

                dest = folder_dir / fname
                # Avoid silently overwriting a different note with the same title
                if dest.exists() and note_id not in done:
                    dest = folder_dir / f"{sanitize(stem)}_{note_id[-6:]}.md"

                dest.write_text(md_text, encoding="utf-8")
                done.add(note_id)
                save_manifest(out, done)   # persist after every note (crash-safe)

                print(f"    ✅  {fname}")
                n_dl += 1

            except Exception as exc:
                print(f"    ❌  {title}  →  {exc}")
                n_err += 1

            time.sleep(args.delay)

    # ── Summary ──
    print(f"\n{'─'*50}")
    print(f"✅  Downloaded : {n_dl}")
    print(f"⏭   Skipped   : {n_skip}")
    print(f"❌  Errors     : {n_err}")
    print(f"📁  Saved to   : {out}")

    if n_err:
        sys.exit(1)


if __name__ == "__main__":
    main()
