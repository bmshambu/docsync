"""LangGraph workflow for Step 1 — Data Prep.

    extract_text → extract_entities (LLM) → build_graph → generate_html

Supports:
  max_docs   — process only the first N documents
  cancel_event — asyncio.Event set by the Stop button; partial results are kept
"""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graphs.state import DataPrepState
from app.llm.extractor import extract_corpus
from app.services import extract as extract_svc
from app.services import graph_build, graph_html, storage


def _emit(state: DataPrepState, message: str, progress: float | None = None, stage: str | None = None):
    fn = state.get("emit")
    if fn:
        fn(message, progress=progress, stage=stage)


# ── Node 1: text extraction ───────────────────────────────────────────────────

def node_extract_text(state: DataPrepState) -> dict:
    settings = get_settings()
    settings.ensure_dirs()
    folder = state["folder_path"]
    max_docs = state.get("max_docs")

    _emit(state, f"Listing documents in {folder} …", progress=0.02, stage="extract_text")
    source = storage.get_source(folder)
    all_paths = source.list_documents()
    if not all_paths:
        raise ValueError(f"No supported documents (.pdf/.docx/.pptx) found in {folder}")

    doc_paths = all_paths[:max_docs] if max_docs else all_paths
    label = f"first {len(doc_paths)}" if max_docs and len(doc_paths) < len(all_paths) else str(len(doc_paths))
    _emit(state, f"Processing {label} of {len(all_paths)} document(s). Extracting text + chunks …",
          progress=0.05, stage="extract_text")

    def on_progress(done, total, result):
        frac = 0.05 + 0.20 * (done / total)
        name = result.get("filename", "?")
        note = "skipped" if result.get("skipped") else f"{result.get('chunks', 0)} chunks"
        if result.get("error"):
            note = f"ERROR: {result['error']}"
        _emit(state, f"[{done}/{total}] {name} — {note}", progress=frac, stage="extract_text")

    results = extract_svc.extract_all(
        doc_paths,
        text_dir=settings.extracted_text_dir,
        chunks_dir=settings.chunks_dir,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        skip_existing=state.get("skip_existing", True),
        on_progress=on_progress,
    )
    return {"doc_paths": [str(p) for p in doc_paths], "extract_results": results}


# ── Node 2: entity/relationship extraction (LLM) ──────────────────────────────

async def node_extract_entities(state: DataPrepState) -> dict:
    settings = get_settings()
    _emit(state, "Extracting entities + relationships with Gemini …",
          progress=0.27, stage="extract_entities")

    def on_progress(done, total, fname, error):
        frac = 0.27 + 0.55 * (done / total)
        note = f"ERROR: {error}" if error else "ok"
        _emit(state, f"[{done}/{total}] {fname} — {note}", progress=frac, stage="extract_entities")

    entities, relationships, per_doc, was_cancelled = await extract_corpus(
        chunks_dir=settings.chunks_dir,
        entities_file=settings.entities_file,
        relationships_file=settings.relationships_file,
        model=settings.model_extract,
        max_concurrency=settings.max_llm_concurrency,
        max_docs=state.get("max_docs"),
        cancel_event=state.get("cancel_event"),
        on_progress=on_progress,
        max_tokens=settings.max_extract_tokens,
    )

    errors = [{"filename": d["filename"], "error": d["error"]} for d in per_doc if d.get("error")]
    msg = (
        f"Extraction stopped early. {len(entities)} entities, {len(relationships)} relationships saved."
        if was_cancelled
        else f"Extracted {len(entities)} entities, {len(relationships)} relationships."
    )
    _emit(state, msg, progress=0.83, stage="extract_entities")
    return {
        "entities_count": len(entities),
        "relationships_count": len(relationships),
        "per_doc_errors": errors,
        "was_cancelled": was_cancelled,
    }


# ── Node 3: build graph + communities ─────────────────────────────────────────

def node_build_graph(state: DataPrepState) -> dict:
    settings = get_settings()
    _emit(state, "Building knowledge graph + detecting communities …",
          progress=0.86, stage="build_graph")

    if not settings.entities_file.exists() or settings.entities_count_on_disk() == 0:
        _emit(state, "No entities to graph — skipping.", progress=0.92, stage="build_graph")
        return {"stats": {"nodes": 0, "edges": 0, "communities": 0}}

    stats = graph_build.build_and_save(
        entities_file=settings.entities_file,
        relationships_file=settings.relationships_file,
        community_map_file=settings.community_map_file,
        graph_stats_file=settings.graph_stats_file,
        resolution=state.get("resolution", 1.0),
    )
    _emit(state, f"Graph: {stats['nodes']} nodes, {stats['edges']} edges, "
                 f"{stats['communities']} communities.", progress=0.92, stage="build_graph")
    return {"stats": stats}


# ── Node 4: generate interactive HTML ─────────────────────────────────────────

def node_generate_html(state: DataPrepState) -> dict:
    settings = get_settings()
    stats = state.get("stats", {})
    if not stats.get("nodes"):
        _emit(state, "No graph to visualise — skipping HTML generation.", progress=1.0, stage="done")
        return {"html_path": ""}

    _emit(state, "Generating interactive graph visualisation …",
          progress=0.95, stage="generate_html")
    out = graph_html.generate_graph_html(
        entities_file=settings.entities_file,
        relationships_file=settings.relationships_file,
        community_map_file=settings.community_map_file,
        communities_dir=settings.communities_dir,
        out_file=settings.graph_html_file,
    )
    suffix = " (partial — stopped early)" if state.get("was_cancelled") else ""
    _emit(state, f"Data prep complete{suffix}.", progress=1.0, stage="done")
    return {"html_path": str(out)}


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_data_prep_graph():
    g = StateGraph(DataPrepState)
    g.add_node("extract_text", node_extract_text)
    g.add_node("extract_entities", node_extract_entities)
    g.add_node("build_graph", node_build_graph)
    g.add_node("generate_html", node_generate_html)
    g.add_edge(START, "extract_text")
    g.add_edge("extract_text", "extract_entities")
    g.add_edge("extract_entities", "build_graph")
    g.add_edge("build_graph", "generate_html")
    g.add_edge("generate_html", END)
    return g.compile()


DATA_PREP_GRAPH = build_data_prep_graph()


async def run_data_prep(
    folder_path: str,
    emit=None,
    cancel_event=None,
    resolution: float = 1.0,
    skip_existing: bool = True,
    max_docs: int | None = None,
) -> dict:
    initial: DataPrepState = {
        "folder_path": folder_path,
        "emit": emit,
        "cancel_event": cancel_event,
        "resolution": resolution,
        "skip_existing": skip_existing,
        "max_docs": max_docs,
    }
    return await DATA_PREP_GRAPH.ainvoke(initial)
