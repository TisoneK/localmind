"""
System Info tool — answers time, date, PC specs offline using Python stdlib.

Handles queries that should NEVER go to web search:
  - Current time / date
  - CPU, RAM, disk specs
  - OS version, hostname, username
  - Running processes (list only, no interaction)
  - Installed Python packages

Registered as Intent.SYSINFO. Fast: no model call, no network, <100ms.
"""
from __future__ import annotations
import datetime
import logging
import os
import platform
import sys

from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)


def _get_system_info(query: str) -> str:
    q = query.lower()
    parts = []

    # Always include time + date — most common query
    now = datetime.datetime.now()
    parts.append(f"Current date/time: {now.strftime('%A, %B %d, %Y  %I:%M:%S %p')}")
    parts.append(f"Timezone: {datetime.datetime.now(datetime.timezone.utc).astimezone().tzname()}")

    wants_specs = any(w in q for w in [
        "spec", "cpu", "ram", "memory", "disk", "storage", "os", "system",
        "computer", "machine", "processor", "hardware", "pc", "laptop"
    ])

    if wants_specs or not any(w in q for w in ["time", "date", "day", "hour", "minute"]):
        parts.append("")
        parts.append(f"OS: {platform.system()} {platform.release()} ({platform.version()})")
        parts.append(f"Machine: {platform.machine()}  |  Processor: {platform.processor()}")
        parts.append(f"Hostname: {platform.node()}")
        parts.append(f"Python: {sys.version.split()[0]}")

        try:
            import psutil
            cpu_count = psutil.cpu_count(logical=True)
            cpu_phys = psutil.cpu_count(logical=False)
            cpu_freq = psutil.cpu_freq()
            freq_str = f"{cpu_freq.current:.0f} MHz" if cpu_freq else "unknown"
            parts.append(f"CPU: {cpu_phys} cores ({cpu_count} logical) @ {freq_str}")

            ram = psutil.virtual_memory()
            parts.append(f"RAM: {ram.total / 1e9:.1f} GB total  |  {ram.available / 1e9:.1f} GB available  ({ram.percent}% used)")

            disk = psutil.disk_usage(os.path.expanduser("~"))
            parts.append(f"Disk (home): {disk.total / 1e9:.1f} GB total  |  {disk.free / 1e9:.1f} GB free")
        except ImportError:
            parts.append("(Install psutil for detailed hardware info: pip install psutil)")

    return "\n".join(parts)


async def sysinfo(query: str) -> ToolResult:
    try:
        q = query.lower()
        # Route installed programs queries to dedicated reader
        if any(w in q for w in ["installed", "programs", "software", "apps", "applications", "packages"]):
            from tools.installed_programs import get_installed_programs
            info = get_installed_programs(query)
        else:
            info = _get_system_info(query)
        return ToolResult(
            content=info,
            risk=RiskLevel.LOW,
            source="sysinfo",
            metadata={"offline": True},
        )
    except Exception as e:
        logger.error(f"[sysinfo] error: {e}")
        return ToolResult(
            content=f"System info error: {e}",
            risk=RiskLevel.LOW,
            source="sysinfo",
        )


register_tool(
    Intent.SYSINFO,
    sysinfo,
    description="Get current time, date, OS version, CPU, RAM, disk specs. Offline, instant.",
    cost=0.0,
    latency_ms=50,
    parallelizable=True,
)
