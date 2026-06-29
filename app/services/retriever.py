"""GraphRAG retrieval — deterministic context assembly for the query agent.

Ported from skills/rfp-query-agent/query_graph.py with paths taken from
Settings instead of hard-coded to a repo root. No LLM calls here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import Settings

_STOP_WORDS = {
    "the", "and", "for", "are", "was", "with", "that", "this", "have",
    "from", "they", "will", "been", "what", "which", "how", "not", "but",
}


# ── Data loading ──────────────────────────────────────────────────────────────

def load_graph_data(settings: Settings) -> tuple[list, list, dict, dict]:
    entities      = json.loads(settings.entities_file.read_text(encoding="utf-8"))
    relationships = json.loads(settings.relationships_file.read_text(encoding="utf-8"))
    community_map = json.loads(settings.community_map_file.read_text(encoding="utf-8"))

    chunks_by_doc: dict[str, list[dict]] = {}
    for f in settings.chunks_dir.glob("*_chunks.json"):
        for c in json.loads(f.read_text(encoding="utf-8")):
            chunks_by_doc.setdefault(c["doc_id"], []).append(c)

    return entities, relationships, community_map, chunks_by_doc


# ── Entity search ─────────────────────────────────────────────────────────────

def search_entities(query: str, entities: list[dict], top_n: int = 10) -> list[dict]:
    keywords = re.findall(r"\w+", query.lower())
    scored = []
    for e in entities:
        text = " ".join([
            e.get("name", ""),
            e.get("type", ""),
            " ".join(e.get("aliases") or []),
            json.dumps(e.get("attributes") or {}),
        ]).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:top_n]]


# ── Graph traversal ───────────────────────────────────────────────────────────

def get_neighbours(entity_ids: set, relationships: list[dict], hops: int = 1) -> dict:
    visited   = set(entity_ids)
    frontier  = set(entity_ids)
    result_rels: list[dict] = []

    for _ in range(hops):
        next_frontier: set = set()
        for r in relationships:
            if r["source"] in frontier or r["target"] in frontier:
                result_rels.append(r)
                next_frontier.add(r["source"])
                next_frontier.add(r["target"])
        frontier  = next_frontier - visited
        visited  |= next_frontier

    return {"entity_ids": list(visited), "relationships": result_rels}


# ── Chunk search ──────────────────────────────────────────────────────────────

def search_chunks(
    query: str,
    chunks_by_doc: dict[str, list[dict]],
    filter_docs: list[str] | None = None,
    top_n: int = 5,
) -> list[dict]:
    keywords = [
        k for k in re.findall(r"\w+", query.lower())
        if k not in _STOP_WORDS and len(k) > 2
    ]
    scored = []
    for doc_id, chunks in chunks_by_doc.items():
        if filter_docs and doc_id not in filter_docs:
            continue
        for chunk in chunks:
            text  = (chunk.get("text") or "").lower()
            score = sum(text.count(kw) for kw in keywords)
            if score > 0:
                scored.append((score, chunk))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_n]]


# ── Community search ──────────────────────────────────────────────────────────

def search_communities(
    query: str,
    community_map: dict,
    communities_dir: Path,
    top_n: int = 3,
) -> list[tuple[str, dict, str]]:
    """Returns list of (comm_id, comm_meta, summary_text) sorted by relevance."""
    keywords = re.findall(r"\w+", query.lower())
    communities = community_map.get("communities", {})
    scored = []

    for comm_id, comm in communities.items():
        entity_text  = " ".join(
            f"{e['name']} {e['type']}" for e in comm.get("entities", [])
        ).lower()
        summary_text = comm.get("summary", "").lower()

        md_file = communities_dir / f"community_{int(comm_id):02d}.md"
        full_summary = ""
        if md_file.exists():
            full_summary = md_file.read_text(encoding="utf-8")
            summary_text += " " + full_summary.lower()

        full_text = entity_text + " " + summary_text
        score = sum(full_text.count(kw) for kw in keywords)
        if score > 0:
            scored.append((score, comm_id, comm, full_summary))

    scored.sort(key=lambda x: -x[0])
    return [(cid, c, s) for _, cid, c, s in scored[:top_n]]


# ── Query classifier ──────────────────────────────────────────────────────────

def classify_query(query: str, matched_entities: list[dict]) -> str:
    q = query.lower()
    global_signals = ["all", "across", "compare", "which rfp", "common", "trend",
                      "every", "both", "overall", "summary", "list all", "how many"]
    local_signals  = ["in the", "for halcyon", "for meridian", "rfp_", "in rfp",
                      "what is", "what are", "specific", "detail"]
    has_global = any(s in q for s in global_signals)
    has_local  = any(s in q for s in local_signals) or len(matched_entities) <= 2
    if has_global and has_local:
        return "hybrid"
    if has_global:
        return "global"
    return "local"


# ── Full retrieval pipeline ───────────────────────────────────────────────────

def retrieve(
    question: str,
    settings: Settings,
    query_type: str = "auto",
    top_chunks: int = 4,
    hops: int = 1,
) -> dict:
    """Assemble all retrieval context for a question. Returns a plain dict."""
    entities, relationships, community_map, chunks_by_doc = load_graph_data(settings)

    matched_entities = search_entities(question, entities)

    if query_type == "auto":
        query_type = classify_query(question, matched_entities)

    # Graph traversal for local / hybrid
    traversal: dict = {"entity_ids": [], "relationships": []}
    if query_type in ("local", "hybrid") and matched_entities:
        seed_ids = {e["id"] for e in matched_entities[:5]}
        traversal = get_neighbours(seed_ids, relationships, hops=hops)

    # Community search for global / hybrid
    relevant_communities: list = []
    if query_type in ("global", "hybrid"):
        relevant_communities = search_communities(
            question, community_map, settings.communities_dir, top_n=3
        )

    # Chunk search — filtered to matched-entity docs when local
    filter_docs: list[str] | None = None
    if query_type == "local" and matched_entities:
        filter_docs = list({
            Path(doc).stem.replace(" ", "_")
            for e in matched_entities[:5]
            for doc in e.get("source_docs", [])
        })

    top_chunk_list = search_chunks(
        question, chunks_by_doc, filter_docs=filter_docs, top_n=top_chunks
    )

    return {
        "query_type": query_type,
        "matched_entities": matched_entities,
        "traversal": traversal,
        "relevant_communities": relevant_communities,  # list of (cid, meta, summary_text)
        "top_chunks": top_chunk_list,
    }
