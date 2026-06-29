"""Step 3 — Query Agent API.

GET  /api/query/prerequisites   → check if graph + communities are ready
POST /api/query/ask             → retrieve context + synthesise answer (direct, no polling)
GET  /api/query/suggestions     → return example questions from the graph
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.llm.query_agent import ask as agent_ask

router = APIRouter(prefix="/api/query", tags=["query"])


# ── Prerequisites ─────────────────────────────────────────────────────────────

@router.get("/prerequisites")
async def prerequisites():
    settings = get_settings()
    has_graph = (
        settings.entities_file.exists()
        and settings.relationships_file.exists()
        and settings.community_map_file.exists()
        and any(settings.chunks_dir.glob("*_chunks.json"))
    )
    if not has_graph:
        return {"ready": False, "entities": 0, "communities": 0, "summaries": 0}

    entity_count = settings.entities_count_on_disk()
    community_map = json.loads(settings.community_map_file.read_text(encoding="utf-8"))
    communities   = community_map.get("communities", {})
    summaries     = sum(1 for c in communities.values() if c.get("summary_file"))

    return {
        "ready": True,
        "entities": entity_count,
        "communities": len(communities),
        "summaries": summaries,
        "summaries_warning": summaries == 0,
    }


# ── Ask ───────────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    query_type: str = "auto"   # auto | local | global | hybrid
    top_chunks: int = 4
    hops: int = 1


@router.post("/ask")
async def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(400, "question is required")

    settings = get_settings()
    if not settings.entities_file.exists():
        raise HTTPException(400, "Graph not ready. Run Data Prep first.")

    try:
        result = await agent_ask(
            question=req.question.strip(),
            settings=settings,
            query_type=req.query_type,
            top_chunks=req.top_chunks,
            hops=req.hops,
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        msg = str(exc)
        if "API key not valid" in msg or "API_KEY_INVALID" in msg:
            raise HTTPException(503, "Invalid Google API key — check GOOGLE_API_KEY in .env")
        if "AuthenticationError" in type(exc).__name__ or ("401" in msg and "azure" in msg.lower()):
            raise HTTPException(503, "Azure OpenAI auth failed — check AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT in .env")
        if "quota" in msg.lower() or "429" in msg:
            raise HTTPException(503, "LLM quota exceeded. Try again later.")
        raise HTTPException(500, f"LLM error: {msg[:200]}")

    return result


# ── Example questions ─────────────────────────────────────────────────────────

@router.get("/suggestions")
async def suggestions():
    settings = get_settings()
    if not settings.entities_file.exists():
        return {"suggestions": []}

    try:
        entities = json.loads(settings.entities_file.read_text(encoding="utf-8"))
    except Exception:
        return {"suggestions": []}

    # Pull a few real entity names to make suggestions concrete
    clients   = [e["name"] for e in entities if e.get("type") == "client"][:2]
    standards = [e["name"] for e in entities if e.get("type") == "standard"][:2]
    techs     = [e["name"] for e in entities if e.get("type") == "technology"][:1]

    base = [
        "Compare requirements across all RFPs",
        "Which RFPs mention security standards?",
        "List all deliverables required",
        "What technologies are mentioned across RFPs?",
        "Summarise the key themes in this corpus",
    ]
    specific = []
    if clients:
        specific.append(f"What does {clients[0]} require?")
    if standards:
        specific.append(f"Which RFPs reference {standards[0]}?")
    if techs:
        specific.append(f"Which vendors use {techs[0]}?")
    if len(clients) > 1:
        specific.append(f"Compare {clients[0]} and {clients[1]}")

    return {"suggestions": (specific + base)[:6]}
