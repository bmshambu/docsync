"""Query agent — retrieval + Gemini synthesis in one async call.

No job queue needed: a typical query completes in 3-8 seconds, well within
an HTTP request timeout. The router awaits this directly.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings
from app.llm.client import get_chat
from app.llm.prompts import build_query_prompt
from app.services.retriever import retrieve


async def ask(
    question: str,
    settings: Settings,
    query_type: str = "auto",
    top_chunks: int = 4,
    hops: int = 1,
) -> dict:
    """Retrieve context from the graph then synthesise a cited answer with Gemini.

    Returns a dict with answer text and retrieval metadata for the UI.
    """
    context = retrieve(question, settings, query_type=query_type,
                       top_chunks=top_chunks, hops=hops)

    system, user = build_query_prompt(question, context)
    chat = get_chat(settings.model_query, temperature=0.1, max_tokens=1024, json_mode=False)
    resp = await chat.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    answer = resp.content if isinstance(resp.content, str) else str(resp.content)

    # Pull out the "Also try:" line so the UI can render it as clickable chips
    also_try: list[str] = []
    lines = answer.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("**Also try:**"):
            raw = line.replace("**Also try:**", "").strip()
            # Split on · delimiter
            also_try = [s.strip().strip('"').strip("'") for s in raw.split("·") if s.strip()]
            # Remove the line from the main answer
            lines.pop(i)
            break
    answer_clean = "\n".join(lines).strip()

    return {
        "answer": answer_clean,
        "also_try": also_try,
        "query_type": context["query_type"],
        "entities_found": len(context["matched_entities"]),
        "communities_used": len(context["relevant_communities"]),
        "chunks_cited": len(context["top_chunks"]),
    }
