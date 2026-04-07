"""
Installed Programs tool — read-only, no interaction with programs.

Lists installed software on Windows (winreg), macOS (brew/Applications),
Linux (dpkg/rpm). Returns a readable list only — never launches, modifies,
or interacts with any program.

Called from tools/sysinfo.py when query mentions "installed" / "programs" / "apps".
"""
from __future__ import annotations
import logging
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _list_windows() -> list[dict]:
    try:
        import winreg
        programs = []
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, key_path in keys:
            try:
                key = winreg.OpenKey(hive, key_path)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                        name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        try: version = winreg.QueryValueEx(sub, "DisplayVersion")[0]
                        except: version = ""
                        try: publisher = winreg.QueryValueEx(sub, "Publisher")[0]
                        except: publisher = ""
                        if name and name.strip():
                            programs.append({"name": name.strip(), "version": version, "publisher": publisher})
                    except: continue
            except: continue
        seen, unique = set(), []
        for p in programs:
            if p["name"].lower() not in seen:
                seen.add(p["name"].lower())
                unique.append(p)
        return sorted(unique, key=lambda x: x["name"].lower())
    except ImportError:
        return []


def _list_linux() -> list[dict]:
    import subprocess
    try:
        out = subprocess.check_output(["dpkg","-l"], stderr=subprocess.DEVNULL, timeout=10).decode("utf-8","replace")
        programs = []
        for line in out.splitlines():
            if line.startswith("ii"):
                parts = line.split()
                if len(parts) >= 3:
                    programs.append({"name": parts[1], "version": parts[2], "publisher": ""})
        if programs: return programs
    except: pass
    try:
        out = subprocess.check_output(["rpm","-qa","--queryformat","%{NAME}|%{VERSION}\n"],
                                       stderr=subprocess.DEVNULL, timeout=10).decode("utf-8","replace")
        programs = []
        for line in out.splitlines():
            parts = line.split("|")
            programs.append({"name": parts[0], "version": parts[1] if len(parts)>1 else "", "publisher": ""})
        if programs: return programs
    except: pass
    return []


def _list_macos() -> list[dict]:
    import subprocess
    from pathlib import Path
    programs = []
    for app in sorted(Path("/Applications").glob("*.app")):
        programs.append({"name": app.stem, "version": "", "publisher": ""})
    try:
        out = subprocess.check_output(["brew","list","--versions"], stderr=subprocess.DEVNULL, timeout=10).decode("utf-8","replace")
        for line in out.splitlines():
            parts = line.split()
            if parts:
                programs.append({"name": parts[0], "version": parts[1] if len(parts)>1 else "", "publisher": "Homebrew"})
    except: pass
    return programs


def get_installed_programs(query: str = "") -> str:
    if sys.platform == "win32":
        programs = _list_windows()
    elif sys.platform == "darwin":
        programs = _list_macos()
    else:
        programs = _list_linux()

    if not programs:
        return "Could not retrieve installed programs on this platform."

    q = query.lower()
    stop = {"installed","programs","apps","applications","software","have","what",
            "list","show","is","are","do","does","i","me","my","the","a","an"}
    filter_terms = [w for w in q.split() if len(w) > 2 and w not in stop]

    if filter_terms:
        filtered = [p for p in programs if any(t in p["name"].lower() for t in filter_terms)]
        if filtered:
            lines = [f"Programs matching '{' '.join(filter_terms)}':"]
            for p in filtered[:30]:
                ver = f" ({p['version']})" if p["version"] else ""
                lines.append(f"  • {p['name']}{ver}")
            return "\n".join(lines)
        return f"No installed programs found matching: {' '.join(filter_terms)}"

    total = len(programs)
    lines = [f"Installed programs ({total} total — first 50 shown):"]
    for p in programs[:50]:
        ver = f" ({p['version']})" if p["version"] else ""
        pub = f" — {p['publisher']}" if p["publisher"] else ""
        lines.append(f"  • {p['name']}{ver}{pub}")
    if total > 50:
        lines.append(f"  … and {total-50} more. Ask about a specific program to search.")
    return "\n".join(lines)
