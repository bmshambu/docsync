"""Community summariser — cloud replacement for the rfp-community-summarizer skill.

For each community in community_map.json:
  1. Gather full entity details, internal + cross-community relationships, top source chunks
  2. Call Gemini to write a structured markdown summary (300-600 words)
  3. Write graph/communities/community_NN.md
  4. Update community_map.json with summary_file pointer (incremental)

Supports cancel_event and max_communities for Stop & Save / batch control.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm.client import get_chat
from app.llm.prompts import build_summary_prompt


# ── Chunk search (keyword-based, same logic as query_graph.py) ────────────────

_STOP_WORDS = {
    "the", "and", "for", "are", "was", "with", "that", "this", "have",
    "from", "they", "will", "been", "what", "which", "how", "not", "but",
}


def _search_chunks(keywords: list[str], chunks_dir: Path, top_n: int = 3) -> list[dict]:
    kws = [k.lower() for k in keywords if k.lower() not in _STOP_WORDS and len(k) > 2]
    if not kws:
        return []
    scored: list[tuple[int, dict]] = []
    for chunk_file in chunks_dir.glob("*_chunks.json"):
        for c in json.loads(chunk_file.read_text(encoding="utf-8")):
            text = (c.get("text") or "").lower()
            score = sum(text.count(k) for k in kws)
            if score > 0:
                scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_n]]


# ── Per-community context builder ─────────────────────────────────────────────

def _build_community_context(
    comm_id: str,
    community: dict,
    entity_lookup: dict[str, dict],
    all_relationships: list[dict],
    community_of: dict[str, str],     # node_id → community_id
    chunks_dir: Path,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Return (full_entities, internal_rels, cross_rels, top_chunks)."""
    member_ids = {e["id"] for e in community.get("entities", []) if e.get("id")}

    # Full entity details
    full_entities = [
        entity_lookup.get(eid, {"id": eid, "name": eid, "type": "unknown",
                                 "source_docs": [], "attributes": {}})
        for eid in member_ids
    ]

    # Split relationships into internal / cross-community
    internal_rels, cross_rels = [], []
    for r in all_relationships:
        src, tgt = r.get("source"), r.get("target")
        if not src or not tgt:
            continue
        src_in = src in member_ids
        tgt_in = tgt in member_ids
        if src_in and tgt_in:
            internal_rels.append(r)
        elif src_in or tgt_in:
            cross_rels.append(r)

    # Keyword search chunks using entity names
    keywords = [e.get("name", "") for e in full_entities if e.get("name")]
    top_chunks = _search_chunks(keywords, chunks_dir, top_n=3)

    return full_entities, internal_rels, cross_rels[:10], top_chunks


# ── LLM call ─────────────────────────────────────────────────────────────────

async def _summarise_one(
    comm_id: str,
    community: dict,
    entity_lookup: dict,
    all_relationships: list[dict],
    community_of: dict,
    chunks_dir: Path,
    communities_dir: Path,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Summarise one community, write its .md file, return result dict."""
    full_entities, internal_rels, cross_rels, top_chunks = _build_community_context(
        comm_id, community, entity_lookup, all_relationships, community_of, chunks_dir
    )

    system, user = build_summary_prompt(
        comm_id=comm_id,
        entities=full_entities,
        internal_rels=internal_rels,
        cross_rels=cross_rels,
        chunk_excerpts=top_chunks,
    )

    chat = get_chat(model, temperature=0.2, max_tokens=2048, json_mode=False)
    async with semaphore:
        resp = await chat.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )

    summary_text = resp.content if isinstance(resp.content, str) else str(resp.content)
    # Strip any accidental code fences the model may add
    summary_text = re.sub(r"^```[a-zA-Z]*\n?", "", summary_text.strip())
    summary_text = re.sub(r"\n?```$", "", summary_text).strip()

    # Write community_NN.md
    communities_dir.mkdir(parents=True, exist_ok=True)
    out_file = communities_dir / f"community_{int(comm_id):02d}.md"
    out_file.write_text(summary_text, encoding="utf-8")

    return {
        "comm_id": comm_id,
        "file": str(out_file),
        "entities": len(full_entities),
        "summary_preview": summary_text[:200],
    }


def _update_community_map(community_map_file: Path, comm_id: str, summary_file: str):
    """Add summary_file pointer to a community entry (incremental update)."""
    try:
        data = json.loads(community_map_file.read_text(encoding="utf-8"))
        if comm_id in data.get("communities", {}):
            data["communities"][comm_id]["summary_file"] = summary_file
        community_map_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass  # non-fatal — the .md file is the real output


# ── Public API ────────────────────────────────────────────────────────────────

async def summarise_corpus(
    community_map_file: Path,
    entities_file: Path,
    relationships_file: Path,
    chunks_dir: Path,
    communities_dir: Path,
    model: str,
    max_concurrency: int,
    max_communities: int | None = None,
    cancel_event: asyncio.Event | None = None,
    on_progress=None,
) -> tuple[list[dict], bool]:
    """Summarise all (or up to max_communities) communities.

    Returns (results_list, was_cancelled).
    """
    community_map = json.loads(community_map_file.read_text(encoding="utf-8"))
    entities      = json.loads(entities_file.read_text(encoding="utf-8"))
    relationships = json.loads(relationships_file.read_text(encoding="utf-8"))

    entity_lookup  = {e["id"]: e for e in entities}
    community_of   = community_map.get("node_to_community", {})
    communities    = community_map.get("communities", {})

    # Sort by community id (numeric), apply max_communities slice
    comm_ids = sorted(communities.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    if max_communities:
        comm_ids = comm_ids[:max_communities]

    total = len(comm_ids)
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[dict] = []
    done = 0
    was_cancelled = False

    async def _run(cid: str) -> dict:
        try:
            return await _summarise_one(
                comm_id=cid,
                community=communities[cid],
                entity_lookup=entity_lookup,
                all_relationships=relationships,
                community_of=community_of,
                chunks_dir=chunks_dir,
                communities_dir=communities_dir,
                model=model,
                semaphore=semaphore,
            )
        except Exception as exc:
            return {"comm_id": cid, "error": str(exc)}

    tasks = [asyncio.create_task(_run(cid)) for cid in comm_ids]

    for coro in asyncio.as_completed(tasks):
        if cancel_event and cancel_event.is_set():
            was_cancelled = True
            for t in tasks:
                t.cancel()
            break

        result = await coro
        results.append(result)
        done += 1

        # Incremental map update
        if not result.get("error"):
            _update_community_map(community_map_file, result["comm_id"], result["file"])

        if on_progress:
            on_progress(done, total, result.get("comm_id"), result.get("error"))

    return results, was_cancelled
