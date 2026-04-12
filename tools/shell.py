"""
tools/shell.py — User-friendly shell tool for LocalMind.

Handles everyday computer tasks in plain English:
  "show my documents", "find my photos", "open Chrome",
  "how much disk space?", "is my internet working?"

Integration contract:
  • Returns ToolResult(content, risk, source) — always, never raises
  • Registered as Intent.SHELL via register_tool()
  • Async wrapper around sync handlers (no blocking event loop)
  • Respects settings.localmind_shell_enabled flag
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

from core.config import settings
from core.models import Intent, RiskLevel, ToolResult
from tools import register_tool

# ---------------------------------------------------------------------------
# PLATFORM — resolved once at import, never guessed
# ---------------------------------------------------------------------------

SYSTEM = platform.system()   # 'Windows', 'Darwin', 'Linux'
HOME   = Path.home()
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"

# ---------------------------------------------------------------------------
# KNOWN FOLDER MAP
# ---------------------------------------------------------------------------

KNOWN_FOLDERS: dict[str, str] = {
    "documents":  "Documents",
    "downloads":  "Downloads",
    "desktop":    "Desktop",
    "pictures":   "Pictures",
    "photos":     "Pictures",
    "images":     "Pictures",
    "music":      "Music",
    "songs":      "Music",
    "videos":     "Videos",
    "movies":     "Videos",
}

FILE_TYPE_EXTENSIONS: dict[str, list[str]] = {
    "photo":       ["jpg", "jpeg", "png", "heic", "raw"],
    "photos":      ["jpg", "jpeg", "png", "heic", "raw"],
    "picture":     ["jpg", "jpeg", "png", "heic", "raw"],
    "image":       ["jpg", "jpeg", "png", "heic", "raw", "gif", "webp"],
    "video":       ["mp4", "mov", "avi", "mkv", "wmv"],
    "music":       ["mp3", "wav", "flac", "aac", "m4a"],
    "document":    ["docx", "doc", "pdf", "odt", "rtf"],
    "pdf":         ["pdf"],
    "spreadsheet": ["xlsx", "xls", "csv", "ods"],
    "tax":         ["pdf", "xlsx", "docx"],
    "receipt":     ["pdf", "jpg", "jpeg", "png"],
}

APP_MAP_WINDOWS: dict[str, str] = {
    "chrome":        "start chrome",
    "firefox":       "start firefox",
    "edge":          "start msedge",
    "word":          "start winword",
    "excel":         "start excel",
    "powerpoint":    "start powerpnt",
    "notepad":       "start notepad",
    "calculator":    "start calc",
    "outlook":       "start outlook",
    "explorer":      "start explorer",
    "files":         "start explorer",
    "paint":         "start mspaint",
    "vlc":           "start vlc",
    "spotify":       "start spotify",
    "teams":         "start teams",
    "zoom":          "start zoom",
    "settings":      "start ms-settings:",
    "control panel": "control",
    "task manager":  "taskmgr",
}

APP_MAP_MAC: dict[str, str] = {
    "chrome":      "open -a 'Google Chrome'",
    "firefox":     "open -a Firefox",
    "safari":      "open -a Safari",
    "word":        "open -a 'Microsoft Word'",
    "excel":       "open -a 'Microsoft Excel'",
    "powerpoint":  "open -a 'Microsoft PowerPoint'",
    "calculator":  "open -a Calculator",
    "outlook":     "open -a 'Microsoft Outlook'",
    "finder":      "open .",
    "files":       "open .",
    "spotify":     "open -a Spotify",
    "vlc":         "open -a VLC",
    "teams":       "open -a 'Microsoft Teams'",
    "zoom":        "open -a zoom.us",
    "settings":    "open 'x-apple.systempreferences:'",
    "mail":        "open -a Mail",
    "notes":       "open -a Notes",
}

APP_MAP_LINUX: dict[str, str] = {
    "chrome":      "xdg-open https://google.com",
    "firefox":     "firefox &",
    "calculator":  "gnome-calculator &",
    "files":       "nautilus &",
    "spotify":     "spotify &",
    "vlc":         "vlc &",
    "settings":    "gnome-control-center &",
}

APP_MAP = (
    APP_MAP_WINDOWS if IS_WIN else
    APP_MAP_MAC     if IS_MAC else
    APP_MAP_LINUX
)

KNOWN_SITES: dict[str, str] = {
    "google":    "https://google.com",
    "amazon":    "https://amazon.com",
    "youtube":   "https://youtube.com",
    "facebook":  "https://facebook.com",
    "gmail":     "https://mail.google.com",
    "outlook":   "https://outlook.com",
    "netflix":   "https://netflix.com",
    "wikipedia": "https://wikipedia.org",
}

BLOCKED_COMMANDS = [
    "format", "del /f", "rm -rf /", "rmdir /s /q c:\\",
    "shutdown", "reboot", ":(){:|:&};:", "dd if=", "mkfs",
    "reg delete", "net user", "net localgroup",
]

# ---------------------------------------------------------------------------
# RESULT HELPERS — internal only, always converted to ToolResult before return
# ---------------------------------------------------------------------------

def _ok(message: str) -> dict:
    return {"success": True, "message": message}

def _err(message: str) -> dict:
    return {"success": False, "message": message}

# ---------------------------------------------------------------------------
# LOW-LEVEL RUNNER
# ---------------------------------------------------------------------------

def _run(cmd: str | list, timeout: int = 20) -> dict:
    """Execute a shell command. Never raises — always returns structured dict."""
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_str.lower():
            return _err(f"That command is blocked for safety: '{blocked}'")
    try:
        result = subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return _ok(result.stdout)
        return _err(result.stderr or "Command returned an error.")
    except subprocess.TimeoutExpired:
        return _err("Command timed out. Try a more specific folder or path.")
    except Exception as exc:
        return _err(f"{exc}\n{traceback.format_exc()}")

# ---------------------------------------------------------------------------
# PATH RESOLUTION
# ---------------------------------------------------------------------------

def _resolve_folder(query: str) -> Path:
    q = query.lower()
    for key, folder in KNOWN_FOLDERS.items():
        if key in q:
            return HOME / folder
    return HOME

# ---------------------------------------------------------------------------
# INTENT HANDLERS — all return plain str (converted to ToolResult in shell_exec)
# ---------------------------------------------------------------------------

def _show_files(query: str) -> tuple[str, RiskLevel]:
    path = _resolve_folder(query)
    if not path.exists():
        return (
            f"Folder not found: {path}\nIt may not exist on this computer.",
            RiskLevel.LOW,
        )
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if not entries:
            return f"The {path.name} folder is empty.", RiskLevel.LOW

        lines = [f"📁  {path}\n"]
        dirs  = [e for e in entries if e.is_dir()]
        files = [e for e in entries if e.is_file()]

        if dirs:
            lines.append(f"Folders ({len(dirs)}):")
            for d in dirs[:20]:
                lines.append(f"  📁  {d.name}")
            if len(dirs) > 20:
                lines.append(f"  … and {len(dirs) - 20} more folders")

        if files:
            lines.append(f"\nFiles ({len(files)}):")
            for f in files[:30]:
                size = _human_size(f.stat().st_size)
                lines.append(f"  📄  {f.name}  ({size})")
            if len(files) > 30:
                lines.append(f"  … and {len(files) - 30} more files")

        return "\n".join(lines), RiskLevel.LOW
    except PermissionError:
        return f"No permission to view {path}.", RiskLevel.LOW


def _find_files(query: str) -> tuple[str, RiskLevel]:
    q = query.lower()

    extensions: list[str] = []
    for keyword, exts in FILE_TYPE_EXTENSIONS.items():
        if keyword in q:
            extensions = exts
            break

    search_root = _resolve_folder(query)

    skip_words = set(
        list(KNOWN_FOLDERS.keys()) +
        list(FILE_TYPE_EXTENSIONS.keys()) +
        ["find", "search", "look", "for", "my", "the", "a", "where", "are", "files", "file"]
    )
    name_keyword = next(
        (w for w in q.split() if w not in skip_words and len(w) > 2),
        None,
    )

    try:
        matches: list[Path] = []
        for ext in (extensions or ["*"]):
            if ext == "*":
                pattern = f"*{name_keyword}*" if name_keyword else "*"
                matches += list(search_root.rglob(pattern))[:100]
            else:
                pattern = f"*{name_keyword}*.{ext}" if name_keyword else f"*.{ext}"
                matches += list(search_root.rglob(pattern))
            if len(matches) > 100:
                break

        matches = list({p for p in matches if p.is_file()})[:50]

        if not matches:
            desc     = f"'{name_keyword}' " if name_keyword else ""
            ext_desc = f".{extensions[0]} files" if extensions else "files"
            return (
                f"No {desc}{ext_desc} found in {search_root}.\n"
                f"Try a different folder or spelling.",
                RiskLevel.LOW,
            )

        lines = [f"Found {len(matches)} file(s):\n"]
        for p in sorted(matches, key=lambda x: x.stat().st_mtime, reverse=True):
            size  = _human_size(p.stat().st_size)
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%b %d, %Y")
            lines.append(f"  📄  {p.name}  ({size}, {mtime})\n      {p.parent}")

        return "\n".join(lines), RiskLevel.LOW
    except PermissionError:
        return f"No permission to search {search_root}.", RiskLevel.LOW


def _open_app(query: str) -> tuple[str, RiskLevel]:
    q = query.lower()
    for app_name, cmd in APP_MAP.items():
        if app_name in q:
            result = _run(cmd)
            if result["success"]:
                return f"✅  Opening {app_name.title()}…", RiskLevel.LOW
            return (
                f"Couldn't open {app_name.title()}. It may not be installed.",
                RiskLevel.MEDIUM,
            )

    url_match = re.search(r'(https?://\S+|www\.\S+)', query)
    if url_match:
        url = url_match.group(1)
        if not url.startswith("http"):
            url = "https://" + url
        cmd = f"start {url}" if IS_WIN else f"open {url}" if IS_MAC else f"xdg-open {url}"
        _run(cmd)
        return f"✅  Opening {url} in your browser…", RiskLevel.LOW

    return (
        "I don't recognise that app.\n"
        "Try: 'open Chrome', 'open Word', 'open Calculator'.",
        RiskLevel.LOW,
    )


def _open_file(query: str) -> tuple[str, RiskLevel]:
    match = re.search(r'[\w\-]+\.\w{2,5}', query)
    if not match:
        return "Please include the file name, e.g. 'open resume.docx'", RiskLevel.LOW

    filename = match.group(0)
    results  = list(HOME.rglob(filename))
    if not results:
        return f"Can't find '{filename}' on this computer.", RiskLevel.LOW

    filepath = results[0]
    cmd = (
        f'start "" "{filepath}"' if IS_WIN else
        f'open "{filepath}"'     if IS_MAC else
        f'xdg-open "{filepath}"'
    )
    _run(cmd)
    return f"✅  Opening {filepath.name} from {filepath.parent}", RiskLevel.LOW


def _check_disk_space() -> tuple[str, RiskLevel]:
    try:
        usage = shutil.disk_usage(HOME)
        pct   = (usage.used / usage.total) * 100
        bar   = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        msg   = (
            f"💾  Disk Space\n\n"
            f"  [{bar}] {pct:.0f}% used\n\n"
            f"  Used:  {_human_size(usage.used)}\n"
            f"  Free:  {_human_size(usage.free)}\n"
            f"  Total: {_human_size(usage.total)}"
        )
        if usage.free < 5 * (1024 ** 3):
            msg += "\n\n⚠️  Low disk space! Consider deleting large files."
        return msg, RiskLevel.LOW
    except Exception as exc:
        return str(exc), RiskLevel.LOW


def _check_internet() -> tuple[str, RiskLevel]:
    cmd = ["ping", "-n", "2", "8.8.8.8"] if IS_WIN else ["ping", "-c", "2", "8.8.8.8"]
    result = _run(cmd, timeout=10)
    return (
        ("✅  Internet is working." if result["success"] else "❌  No internet connection detected."),
        RiskLevel.LOW,
    )


def _get_ip_address() -> tuple[str, RiskLevel]:
    result = _run("ipconfig" if IS_WIN else ("ifconfig" if IS_MAC else "ip addr show"))
    if not result["success"]:
        return "Couldn't retrieve IP address.", RiskLevel.LOW

    ips = [
        m.group(1)
        for line in result["message"].splitlines()
        if (m := re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', line))
        and not m.group(1).startswith(("127.", "255."))
    ]
    return (
        (f"🌐  Your IP address: {ips[0]}" if ips else "Couldn't determine IP address."),
        RiskLevel.LOW,
    )


def _get_system_info() -> tuple[str, RiskLevel]:
    lines = [
        "💻  System Information\n",
        f"  Computer: {platform.node()}",
        f"  OS:       {SYSTEM} {platform.release()}",
        f"  Version:  {platform.version()[:60]}",
        f"  User:     {HOME.name}",
        f"  Home:     {HOME}",
    ]
    try:
        import psutil
        lines.append(
            f"  RAM:      {_human_size(psutil.virtual_memory().available)} free "
            f"of {_human_size(psutil.virtual_memory().total)}"
        )
    except ImportError:
        pass
    return "\n".join(lines), RiskLevel.LOW


def _open_website(query: str) -> tuple[str, RiskLevel]:
    q = query.lower()
    for name, url in KNOWN_SITES.items():
        if name in q:
            cmd = f"start {url}" if IS_WIN else f"open {url}" if IS_MAC else f"xdg-open {url}"
            _run(cmd)
            return f"✅  Opening {name.title()} in your browser…", RiskLevel.LOW

    url_match = re.search(r'([\w\-]+\.(?:com|org|net|io|co|gov|edu)\S*)', query)
    if url_match:
        url = "https://" + url_match.group(1)
        cmd = f"start {url}" if IS_WIN else f"open {url}" if IS_MAC else f"xdg-open {url}"
        _run(cmd)
        return f"✅  Opening {url}…", RiskLevel.LOW

    return "I couldn't find a website in that request.", RiskLevel.LOW

# ---------------------------------------------------------------------------
# DISPATCHER
# ---------------------------------------------------------------------------

def _dispatch(query: str) -> tuple[str, RiskLevel]:
    """Route plain-English query to the correct handler. Never raises."""
    q = query.lower()

    if any(w in q for w in ["show my", "list", "browse", "what's in", "whats in", "see my"]):
        if any(w in q for w in list(KNOWN_FOLDERS.keys()) + ["files", "folder", "directory"]):
            return _show_files(query)

    if any(w in q for w in ["find", "search", "look for", "where are", "where is", "locate"]):
        return _find_files(query)

    if any(w in q for w in ["open", "start", "launch", "run"]):
        if re.search(r'\.\w{2,5}\b', query):
            return _open_file(query)
        return _open_app(query)

    if any(w in q for w in ["go to", "visit", "website"] + list(KNOWN_SITES.keys())):
        return _open_website(query)

    if any(w in q for w in ["disk space", "storage", "how much space", "hard drive", "free space"]):
        return _check_disk_space()

    if any(w in q for w in ["internet", "wifi", "wi-fi", "connection", "online", "network"]):
        return _check_internet()

    if any(w in q for w in ["ip address", "my ip", "ip addr"]):
        return _get_ip_address()

    if any(w in q for w in ["system info", "about my computer", "windows version", "os version", "computer info"]):
        return _get_system_info()

    return (
        "I didn't understand that request.\n"
        "Try things like:\n"
        "  • 'show my documents'\n"
        "  • 'find my vacation photos'\n"
        "  • 'open Chrome'\n"
        "  • 'how much disk space do I have?'\n"
        "  • 'is my internet working?'",
        RiskLevel.LOW,
    )

# ---------------------------------------------------------------------------
# ASYNC ENTRY POINT — what LocalMind's dispatch layer calls
# ---------------------------------------------------------------------------

async def shell_exec(message: str) -> ToolResult:
    """
    LocalMind tool entry point for Intent.SHELL.

    Always returns a ToolResult — never raises, never fabricates.
    """
    if not getattr(settings, "localmind_shell_enabled", False):
        return ToolResult(
            content="Shell tool is disabled. Set LOCALMIND_SHELL_ENABLED=true in .env to enable it.",
            risk=RiskLevel.LOW,
            source="shell",
            success=False,
            error_type="permission",
            error_message="Shell tool disabled via LOCALMIND_SHELL_ENABLED.",
        )

    try:
        # Run sync handlers in a thread so we don't block the event loop
        content, risk = await asyncio.get_event_loop().run_in_executor(
            None, _dispatch, message
        )
        return ToolResult(content=content, risk=risk, source="shell")

    except Exception as exc:
        return ToolResult(
            content=f"[TOOL ERROR] Unexpected shell failure: {exc}",
            risk=RiskLevel.HIGH,
            source="shell",
            metadata={"traceback": traceback.format_exc()},
            success=False,
            error_type="internal_error",
            error_message=str(exc),
        )

# ---------------------------------------------------------------------------
# UTILITY
# ---------------------------------------------------------------------------

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

# ---------------------------------------------------------------------------
# SELF-REGISTER
# ---------------------------------------------------------------------------

register_tool(
    Intent.SHELL,
    shell_exec,
    description=(
        "User-friendly shell for everyday computer tasks. "
        "Handles: 'show my documents', 'find my photos', 'open Chrome', "
        "'how much disk space?', 'is my internet working?', 'what's my IP?'"
    ),
    cost=0.03,
    latency_ms=3000,
    parallelizable=False,
)