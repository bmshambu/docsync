"""Shared LangGraph state types."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, TypedDict

EmitFn = Callable[..., None]


class CommunitySummaryState(TypedDict, total=False):
    # Inputs
    emit: Optional[EmitFn]
    cancel_event: Optional[Any]
    max_communities: Optional[int]

    # Produced
    community_ids: list[str]
    results: list[dict]
    was_cancelled: bool
    errors: list[dict]


class DataPrepState(TypedDict, total=False):
    # Inputs
    folder_path: str
    resolution: float
    skip_existing: bool
    max_docs: Optional[int]          # None = process all
    emit: Optional[EmitFn]
    cancel_event: Optional[Any]      # asyncio.Event — not JSON-serialisable but fine in memory

    # Produced by nodes
    doc_paths: list[str]
    extract_results: list[dict]
    entities_count: int
    relationships_count: int
    per_doc_errors: list[dict]
    was_cancelled: bool
    stats: dict[str, Any]
    html_path: str
    errors: list[str]
