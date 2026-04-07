"""
File system utility routes.

POST /api/open-file  — open a file with the OS default program
POST /api/confirm-write — confirm a pending permission-gated file write
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import settings

router = APIRouter()


class OpenFileRequest(BaseModel):
    path: str


class ConfirmWriteRequest(BaseModel):
    path: str
    content: str


@router.post("/open-file")
async def open_file(req: OpenFileRequest):
    """Open a file using the OS default application."""
    path = Path(req.path).resolve()

    # Safety: only open files within allowed paths
    if not settings.is_path_allowed(path):
        raise HTTPException(
            status_code=403,
            detail=f"Path is outside allowed folders: {path}",
        )

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        if sys.platform == "win32":
            subprocess.Popen(["start", "", str(path)], shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"opened": str(path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open file: {e}")


@router.post("/confirm-write")
async def confirm_write(req: ConfirmWriteRequest):
    """Execute a previously permission-gated file write."""
    from tools.file_writer import _do_write
    path = Path(req.path).resolve()

    if not settings.is_path_allowed(path):
        raise HTTPException(status_code=403, detail="Path is outside allowed folders")

    result = await _do_write(path, req.content)
    return {
        "path": result.metadata.get("path", ""),
        "filename": result.metadata.get("filename", ""),
        "bytes": result.metadata.get("bytes", 0),
        "content": result.content,
    }
