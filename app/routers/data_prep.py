"""Step 1 — Data Prep API.

GET  /api/data-prep/scan            → count + names of docs in a folder (no LLM)
POST /api/data-prep/run             → start a background data-prep job
POST /api/data-prep/cancel/{id}     → request stop-and-save for a running job
GET  /api/data-prep/status/{id}     → poll job progress + logs + result
GET  /api/data-prep/graph-stats     → latest graph_stats.json (if any)
GET  /api/data-prep/graph-html      → the generated interactive graph HTML
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.graphs.data_prep_graph import run_data_prep
from app.jobs import job_manager
from app.services.storage import SUPPORTED_EXTENSIONS, get_source

router = APIRouter(prefix="/api/data-prep", tags=["data-prep"])


# ── Scan ──────────────────────────────────────────────────────────────────────

@router.get("/scan")
async def scan(folder_path: str = Query(..., description="Absolute path to a folder of RFP docs")):
    folder_path = folder_path.strip()
    if not folder_path:
        raise HTTPException(400, "folder_path is required")
    try:
        source = get_source(folder_path)
        docs = source.list_documents()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(400, str(exc))

    by_type: dict[str, int] = {}
    for d in docs:
        ext = d.suffix.lower().lstrip(".")
        by_type[ext] = by_type.get(ext, 0) + 1

    return {
        "count": len(docs),
        "by_type": by_type,
        "files": [d.name for d in docs],
    }


# ── Run ───────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    folder_path: str
    resolution: float = 1.0
    skip_existing: bool = True
    max_docs: int | None = None   # None = process all


@router.post("/run")
async def start_run(req: RunRequest):
    folder = req.folder_path.strip()
    if not folder:
        raise HTTPException(400, "folder_path is required")

    job = job_manager.create(kind="data_prep")

    async def factory(emit, cancel_event):
        return await run_data_prep(
            folder_path=folder,
            emit=emit,
            cancel_event=cancel_event,
            resolution=req.resolution,
            skip_existing=req.skip_existing,
            max_docs=req.max_docs,
        )

    job_manager.run(job, factory)
    return {"job_id": job.id, "status": job.status}


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.post("/cancel/{job_id}")
async def cancel(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status not in ("running", "cancelling"):
        return {"status": job.status, "message": "Job is not running"}
    job.cancel()
    return {"status": "cancelling"}


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status/{job_id}")
async def status(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job.to_dict()


# ── Outputs ───────────────────────────────────────────────────────────────────

@router.get("/graph-stats")
async def graph_stats():
    settings = get_settings()
    if not settings.graph_stats_file.exists():
        return JSONResponse({"exists": False})
    stats = json.loads(settings.graph_stats_file.read_text(encoding="utf-8"))
    stats["exists"] = True
    return stats


@router.get("/graph-html")
async def graph_html():
    settings = get_settings()
    if not settings.graph_html_file.exists():
        raise HTTPException(404, "Graph not generated yet. Run data prep first.")
    return FileResponse(settings.graph_html_file, media_type="text/html")
