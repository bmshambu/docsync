# RFP GraphRAG — Cloud App (FastAPI + LangGraph + Gemini / Azure OpenAI)

Cloud port of the Claude Cowork "Smart RAG / DocSync" skills. The desktop skills
relied on Claude Cowork as the intelligence layer; this app replaces that with
**LangGraph workflows** that call either **Google Gemini 2.5 Flash** or
**Azure OpenAI** — switchable via a single `.env` setting.

All three tabs are fully implemented:

| Tab | Original skill | What it does |
|---|---|---|
| 1 · Data Prep | `rfp-data-prep` | Scan → extract text/chunks → extract entities & relationships via LLM → build NetworkX+Louvain graph → generate D3 visualisation |
| 2 · Community Summariser | `rfp-community-summarizer` | For each Louvain community, write a plain-English markdown summary via LLM |
| 3 · Query Agent | `rfp-query-agent` | Natural-language Q&A — retrieves from the graph and synthesises a cited answer via LLM |

---

## Architecture

```
rfp_data/  (your documents)
    │
    ▼  Tab 1 — Data Prep  (LangGraph: 4 nodes)
    ├─ extract_text     →  data/extracted_text/  +  data/chunks/
    ├─ extract_entities →  data/graph/entities.json  +  relationships.json   (LLM)
    ├─ build_graph      →  data/graph/community_map.json  +  graph_stats.json
    └─ generate_html    →  data/graph/knowledge_graph.html
    │
    ▼  Tab 2 — Community Summariser  (LangGraph: 2 nodes)
    └─ summarise        →  data/graph/communities/community_NN.md            (LLM)
    │
    ▼  Tab 3 — Query Agent  (single async call)
    └─ retrieve + ask   →  entity search + graph traversal + Gemini/Azure    (LLM)
                           → answer with page-level citations
```

Key design decisions:

- **Scan-first UX** — folder is scanned instantly before any LLM work; user picks how many docs to process (First 10 / 50 / 100 / All).
- **Stop & Save** — cancel mid-run at any time; incremental writes after each doc/community mean partial results are always valid.
- **Async concurrency** — LLM calls run in parallel up to `MAX_LLM_CONCURRENCY` (default 5).
- **Dual provider** — one `.env` line switches between Google Gemini and Azure OpenAI; no code changes needed.

---

## LLM provider

Set `LLM_PROVIDER` in `.env`:

### Google Gemini (default)

```env
LLM_PROVIDER=google
GOOGLE_API_KEY=AIzaSy-xxxx        # from https://aistudio.google.com/app/apikey
MODEL_EXTRACT=gemini-2.5-flash
MODEL_SUMMARY=gemini-2.5-flash
MODEL_QUERY=gemini-2.5-flash
```

### Azure OpenAI

```env
LLM_PROVIDER=azure_openai
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o    # your deployment name (used for all three steps)
AZURE_OPENAI_API_VERSION=2024-02-15-preview
```

The header pill in the UI updates automatically to show the active provider and model.

---

## Setup (local)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

copy .env.example .env            # Windows
# cp .env.example .env            # macOS / Linux
# Then edit .env — set your API key and LLM_PROVIDER
```

### Run

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

---

## What to deploy

Only these folders/files are needed — everything else is created at runtime:

```
Claude-cowork-azure/
├── app/               ← all Python application code
├── static/            ← CSS + JS
├── templates/         ← Jinja2 HTML templates
├── requirements.txt
└── .env               ← your API keys (never commit this)
```

| Path | Notes |
|---|---|
| `data/` | Created at runtime by the pipeline — do not deploy |
| `skills/` | No longer needed — D3 generator is bundled inside `app/services/` |
| `.venv/` | Recreated by `pip install -r requirements.txt` on the server |
| `.claude/` | Claude Code tooling only |

---

## Azure deployment

**Startup command** (App Service / Container Apps):

```bash
pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Environment variables to set in Azure:**

| Variable | Value |
|---|---|
| `LLM_PROVIDER` | `google` or `azure_openai` |
| `GOOGLE_API_KEY` | your Gemini key (if using Google) |
| `AZURE_OPENAI_API_KEY` | your Azure key (if using Azure) |
| `AZURE_OPENAI_ENDPOINT` | your Azure endpoint |
| `AZURE_OPENAI_DEPLOYMENT` | your deployment name |
| `DATA_DIR` | path to a persistent volume (e.g. `/mnt/data`) |

**Notes for production:**

- Mount a persistent volume at `DATA_DIR` so processed graph data survives container restarts.
- The in-memory `JobManager` works for single-instance deployments. For multi-instance scaling, replace it with a durable store (Redis, Azure Table Storage, etc.).
- Swap `FolderSource` in `app/services/storage.py` for an `AzureBlobSource` to let users point at Blob containers instead of local paths.

---

## Project layout

```
app/
  config.py                    # Settings (pydantic-settings) + derived path helpers + provider helpers
  main.py                      # FastAPI app — mounts routers, serves 3-tab UI
  jobs.py                      # In-memory job manager with asyncio.Event cancel support
  services/
    storage.py                 # FolderSource — list documents from a local path
    extract.py                 # Text + chunk extraction (PDF / DOCX / PPTX)
    graph_build.py             # NetworkX graph + Louvain community detection
    graph_html.py              # Thin wrapper calling the D3 generator
    graph_html_generator.py    # Self-contained D3.js HTML generator (bundled)
  llm/
    client.py                  # get_chat() factory — returns Gemini or AzureChatOpenAI
    prompts.py                 # Prompt builders for extraction, summary, and query
    extractor.py               # Async per-doc entity/relationship extraction + incremental writes
    summarizer.py              # Async per-community summary generation + incremental writes
    query_agent.py             # Retrieval + LLM synthesis for Tab 3
  graphs/
    state.py                   # LangGraph TypedDicts (DataPrepState, CommunitySummaryState)
    data_prep_graph.py         # Tab 1 StateGraph (4 nodes)
    community_graph.py         # Tab 2 StateGraph (2 nodes)
  routers/
    data_prep.py               # /api/data-prep/* — scan, run, cancel, status, graph-html
    community.py               # /api/community/* — prerequisites, run, cancel, status, summaries
    query.py                   # /api/query/*    — prerequisites, ask, suggestions
templates/
  base.html                    # Header + tab shell
  index.html                   # 3-tab content (Data Prep / Community / Query)
static/
  css/style.css                # Dark theme, progress bar, chat bubbles, chips, accordion
  js/app.js                    # Tab switching + all client-side logic for all three tabs
data/                          # Runtime working directory (gitignored)
  extracted_text/
  chunks/
  graph/
    entities.json
    relationships.json
    community_map.json
    graph_stats.json
    knowledge_graph.html
    communities/
      community_00.md …
```
