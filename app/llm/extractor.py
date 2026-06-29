"""LLM-driven entity/relationship extraction.

Fixes vs original:
  - Gemini JSON mode (response_mime_type=application/json) prevents malformed output.
  - json-repair as a fallback for any remaining parse errors.
  - max_docs: slice corpus to process only the first N documents.
  - cancel_event: asyncio.Event — set by the Stop button; extraction stops between
    docs, partial results are written to disk, and the graph still builds.
  - Incremental write: entities/relationships flushed to disk after every document
    so a cancelled run always leaves a valid (partial) graph on disk.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import json_repair
from langchain_core.messages import HumanMessage, SystemMessage

from app.llm.client import get_chat
from app.llm.prompts import build_extraction_prompt

_MAX_WORDS = 24_000


def _page_marked_text(chunks: list[dict]) -> str:
    parts: list[str] = []
    last_page = None
    for c in chunks:
        page = c.get("page_start")
        if page != last_page:
            parts.append(f"\n[page={page}]")
            last_page = page
        parts.append(c.get("text", ""))
    text = " ".join(parts)
    words = text.split()
    return " ".join(words[:_MAX_WORDS]) if len(words) > _MAX_WORDS else text


def _parse_json_object(raw: str) -> dict:
    """Robust JSON parse with three fallback layers."""
    raw = raw.strip()
    # Strip markdown fences (Gemini sometimes adds them despite JSON mode)
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()

    # Layer 1: strict parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Layer 2: json-repair (handles missing/trailing commas, unquoted keys, etc.)
    try:
        repaired = json_repair.repair_json(raw, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
    except Exception:
        pass

    # Layer 3: extract outermost {...} span and strict-parse that
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot parse JSON from model output (first 300 chars): {raw[:300]}")


async def extract_one(
    filename: str,
    chunks: list[dict],
    model: str,
    semaphore: asyncio.Semaphore,
    max_tokens: int = 16000,
) -> dict:
    system, user = build_extraction_prompt(filename, _page_marked_text(chunks))
    chat = get_chat(model, temperature=0.0, max_tokens=max_tokens, json_mode=True)

    async with semaphore:
        resp = await chat.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )

    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _parse_json_object(content)
    return {
        "filename": filename,
        "entities": data.get("entities") or [],
        "relationships": data.get("relationships") or [],
    }


def _load_chunks_by_doc(chunks_dir: Path) -> dict[str, list[dict]]:
    by_doc: dict[str, list[dict]] = {}
    for f in sorted(chunks_dir.glob("*_chunks.json")):
        for c in json.loads(f.read_text(encoding="utf-8")):
            by_doc.setdefault(c["filename"], []).append(c)
    return by_doc


def merge_entities(per_doc: list[dict]) -> tuple[list[dict], list[dict]]:
    entities_by_id: dict[str, dict] = {}
    relationships: list[dict] = []
    for doc_result in per_doc:
        for e in doc_result.get("entities") or []:
            eid = e.get("id")
            if not eid:
                continue
            if eid not in entities_by_id:
                entities_by_id[eid] = {
                    "id": eid,
                    "name": e.get("name", eid),
                    "type": e.get("type", "unknown"),
                    "aliases": list(e.get("aliases") or []),
                    "source_docs": list(e.get("source_docs") or []),
                    "attributes": dict(e.get("attributes") or {}),
                }
            else:
                ex = entities_by_id[eid]
                ex["aliases"] = sorted(set(ex["aliases"]) | set(e.get("aliases") or []))
                ex["source_docs"] = sorted(set(ex["source_docs"]) | set(e.get("source_docs") or []))
                ex["attributes"].update(e.get("attributes") or {})
        relationships.extend(doc_result.get("relationships") or [])
    return list(entities_by_id.values()), relationships


def _save_incremental(
    per_doc: list[dict],
    entities_file: Path,
    relationships_file: Path,
) -> tuple[list[dict], list[dict]]:
    """Merge + write entities/relationships to disk after each doc."""
    entities, relationships = merge_entities(per_doc)
    entities_file.parent.mkdir(parents=True, exist_ok=True)
    entities_file.write_text(json.dumps(entities, indent=2, ensure_ascii=False), encoding="utf-8")
    relationships_file.write_text(json.dumps(relationships, indent=2, ensure_ascii=False), encoding="utf-8")
    return entities, relationships


async def extract_corpus(
    chunks_dir: Path,
    entities_file: Path,
    relationships_file: Path,
    model: str,
    max_concurrency: int,
    max_docs: int | None = None,
    cancel_event: asyncio.Event | None = None,
    on_progress=None,
    max_tokens: int = 16000,
) -> tuple[list[dict], list[dict], list[dict], bool]:
    """Extract entities/relationships for every (or up to max_docs) documents.

    Writes incrementally to disk after each doc.
    Returns (entities, relationships, per_doc_results, was_cancelled).
    """
    chunks_by_doc = _load_chunks_by_doc(chunks_dir)
    filenames = list(chunks_by_doc.keys())
    if max_docs:
        filenames = filenames[:max_docs]

    total = len(filenames)
    semaphore = asyncio.Semaphore(max_concurrency)
    per_doc: list[dict] = []
    done = 0
    was_cancelled = False
    entities: list[dict] = []
    relationships: list[dict] = []

    async def _run(fname: str) -> dict:
        try:
            return await extract_one(fname, chunks_by_doc[fname], model, semaphore, max_tokens=max_tokens)
        except Exception as exc:
            return {"filename": fname, "entities": [], "relationships": [], "error": str(exc)}

    tasks = {asyncio.create_task(_run(f)): f for f in filenames}

    for coro in asyncio.as_completed(list(tasks)):
        # Check for cancellation before accepting each result
        if cancel_event and cancel_event.is_set():
            was_cancelled = True
            # Cancel all tasks still waiting on the semaphore
            for t in tasks:
                t.cancel()
            break

        result = await coro
        per_doc.append(result)
        done += 1

        # Incremental write — even a cancelled run leaves a valid partial graph
        entities, relationships = _save_incremental(per_doc, entities_file, relationships_file)

        if on_progress:
            on_progress(done, total, result.get("filename"), result.get("error"))

    # If no docs at all, write empty files so downstream nodes don't crash
    if not entities_file.exists():
        _save_incremental([], entities_file, relationships_file)

    return entities, relationships, per_doc, was_cancelled
