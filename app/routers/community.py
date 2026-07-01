"""Step 2 — Community Summariser API.

GET  /api/community/prerequisites   → check if data prep ran (counts communities)
POST /api/community/run             → start summarisation job
POST /api/community/cancel/{id}     → stop & save
GET  /api/community/status/{id}     → poll progress
GET  /api/community/summaries       → list written community .md files with previews
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.graphs.community_graph import run_community_summary
from app.jobs import job_manager

router = APIRouter(prefix="/api/community", tags=["community"])


# ── Prerequisites check ───────────────────────────────────────────────────────

@router.get("/prerequisites")
async def prerequisites():
    settings = get_settings()
    ready = (
        settings.community_map_file.exists()
        and settings.entities_file.exists()
        and settings.relationships_file.exists()
        and any(settings.chunks_dir.glob("*_chunks.json"))
    )
    if not ready:
        return {"ready": False, "community_count": 0, "summaries_done": 0}

    community_map = json.loads(settings.community_map_file.read_text(encoding="utf-8"))
    communities = community_map.get("communities", {})
    summaries_done = sum(1 for c in communities.values() if c.get("summary_file"))
    return {
        "ready": True,
        "community_count": len(communities),
        "summaries_done": summaries_done,
        "communities": [
            {
                "id": cid,
                "entity_count": len(c.get("entities", [])),
                "source_docs": c.get("source_docs", []),
                "has_summary": bool(c.get("summary_file")),
            }
            for cid, c in sorted(
                communities.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0
            )
        ],
    }


# ── Run ───────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    max_communities: int | None = None


@router.post("/run")
async def start_run(req: RunRequest):
    job = job_manager.create(kind="community_summary")

    async def factory(emit, cancel_event):
        return await run_community_summary(
            emit=emit,
            cancel_event=cancel_event,
            max_communities=req.max_communities,
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


# ── Summaries list ────────────────────────────────────────────────────────────

@router.get("/summaries")
async def summaries():
    settings = get_settings()
    if not settings.communities_dir.exists():
        return {"summaries": []}
    items = []
    for md_file in sorted(settings.communities_dir.glob("community_*.md")):
        text = md_file.read_text(encoding="utf-8")
        title = md_file.stem
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break
            # Handle heading with no trailing space, or single # without space
            if stripped.startswith("#") and not stripped.startswith("##") and len(stripped) > 1:
                title = stripped.lstrip("#").strip()
                break
        items.append({"file": md_file.name, "title": title, "preview": text[:400].strip()})
    return {"summaries": items}
