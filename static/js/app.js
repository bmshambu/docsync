// ── Tab switching ────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    const panelId = "tab-" + tab.dataset.tab;
    document.getElementById(panelId).classList.add("active");
    // Trigger tab-specific init
    if (tab.dataset.tab === "community") initCommunityTab();
    if (tab.dataset.tab === "query") initQueryTab();
  });
});

// ── Element refs ─────────────────────────────────────────────────
const folderInput   = document.getElementById("folder-path");
const scanBtn       = document.getElementById("scan-btn");
const scanResult    = document.getElementById("scan-result");
const scanSummary   = document.getElementById("scan-summary");
const batchOptions  = document.getElementById("batch-options");
const runBtn        = document.getElementById("run-btn");
const progressCard  = document.getElementById("progress-card");
const resultCard    = document.getElementById("result-card");
const stopBtn       = document.getElementById("stop-btn");
const logEl         = document.getElementById("log");
const fillEl        = document.getElementById("progress-fill");
const pctEl         = document.getElementById("progress-pct");
const stageEl       = document.getElementById("progress-stage");
const partialBadge  = document.getElementById("partial-badge");

let selectedMaxDocs = null;   // null = all
let totalDocCount   = 0;
let currentJobId    = null;
let pollTimer       = null;

// ── Step 1: Scan folder ──────────────────────────────────────────
scanBtn.addEventListener("click", async () => {
  const folder = folderInput.value.trim();
  if (!folder) { folderInput.focus(); return; }

  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning…";
  scanResult.classList.add("hidden");

  try {
    const res = await fetch("/api/data-prep/scan?" + new URLSearchParams({ folder_path: folder }));
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderScanResult(data);
  } catch (err) {
    alert("Scan error: " + err.message);
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan folder";
  }
});

function renderScanResult(data) {
  totalDocCount = data.count;
  selectedMaxDocs = null;   // default = all

  const typeStr = Object.entries(data.by_type || {})
    .map(([ext, n]) => `${ext.toUpperCase()} ×${n}`)
    .join(" · ");

  scanSummary.innerHTML = `
    <span class="count">${data.count}</span>
    <span>document${data.count !== 1 ? "s" : ""} found</span>
    ${typeStr ? `<span class="types">${typeStr}</span>` : ""}
  `;

  // Build batch buttons dynamically based on total count
  const thresholds = [10, 50, 100].filter((n) => n < data.count);
  batchOptions.innerHTML = "";

  thresholds.forEach((n) => {
    const btn = makeBatchBtn(`First ${n}`, n);
    batchOptions.appendChild(btn);
  });
  // "All" is always present and selected by default
  const allBtn = makeBatchBtn(`All ${data.count}`, null);
  allBtn.classList.add("selected");
  batchOptions.appendChild(allBtn);

  scanResult.classList.remove("hidden");
  runBtn.disabled = false;
}

function makeBatchBtn(label, maxDocs) {
  const btn = document.createElement("button");
  btn.className = "batch-btn";
  btn.textContent = label;
  btn.addEventListener("click", () => {
    document.querySelectorAll(".batch-btn").forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
    selectedMaxDocs = maxDocs;
  });
  return btn;
}

// ── Step 2: Run ──────────────────────────────────────────────────
runBtn.addEventListener("click", async () => {
  const folder = folderInput.value.trim();
  if (!folder) return;

  runBtn.disabled = true;
  runBtn.textContent = "Running…";
  stopBtn.disabled = false;
  resultCard.classList.add("hidden");
  partialBadge.classList.add("hidden");
  progressCard.classList.remove("hidden");
  logEl.textContent = "";
  setProgress(0, "Starting…");

  try {
    const res = await fetch("/api/data-prep/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_path: folder,
        resolution: parseFloat(document.getElementById("resolution").value) || 1.0,
        skip_existing: document.getElementById("skip-existing").checked,
        max_docs: selectedMaxDocs,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    currentJobId = data.job_id;
    pollStatus(currentJobId);
  } catch (err) {
    appendLog("ERROR: " + err.message);
    resetRunBtn();
  }
});

// ── Stop & Save ──────────────────────────────────────────────────
stopBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  stopBtn.disabled = true;
  stopBtn.textContent = "Stopping…";
  try {
    await fetch(`/api/data-prep/cancel/${currentJobId}`, { method: "POST" });
    appendLog("Stop requested — finishing in-flight docs then building partial graph…");
  } catch (err) {
    appendLog("Stop error: " + err.message);
  }
});

// ── Polling ──────────────────────────────────────────────────────
function pollStatus(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res  = await fetch(`/api/data-prep/status/${jobId}`);
      const job  = await res.json();
      renderLogs(job.logs);
      setProgress(job.progress * 100, prettyStage(job.stage));

      if (job.status === "completed") {
        clearInterval(pollTimer);
        renderResult(job.result);
        resetRunBtn();
        stopBtn.disabled = true;
      } else if (job.status === "failed") {
        clearInterval(pollTimer);
        appendLog("FAILED: " + (job.error || "unknown error"));
        resetRunBtn();
        stopBtn.disabled = true;
      }
    } catch (err) {
      appendLog("Polling error: " + err.message);
    }
  }, 1200);
}

// ── Render helpers ───────────────────────────────────────────────
function renderLogs(logs) {
  if (!logs?.length) return;
  logEl.textContent = logs.map((l) => l.message).join("\n");
  logEl.scrollTop = logEl.scrollHeight;
}

function appendLog(msg) {
  logEl.textContent += (logEl.textContent ? "\n" : "") + msg;
  logEl.scrollTop = logEl.scrollHeight;
}

function setProgress(pct, stage) {
  const p = Math.max(0, Math.min(100, pct));
  fillEl.style.width = p + "%";
  pctEl.textContent = Math.round(p) + "%";
  if (stage) stageEl.textContent = stage;
}

function prettyStage(stage) {
  return {
    start:            "Starting…",
    extract_text:     "Extracting text + chunks",
    extract_entities: "Extracting entities with Gemini",
    build_graph:      "Building graph + communities",
    generate_html:    "Generating visualisation",
    cancelling:       "Stopping — finishing in-flight docs…",
    done:             "Complete",
    error:            "Error",
  }[stage] || stage || "";
}

function renderResult(result) {
  if (!result) return;
  const stats = result.stats || {};

  if (result.was_cancelled) {
    partialBadge.classList.remove("hidden");
  }

  const docsProcessed = (result.doc_paths || []).length;
  const grid = document.getElementById("stats-grid");
  const cells = [
    ["Documents", docsProcessed + (result.was_cancelled ? ` / ${totalDocCount}` : "")],
    ["Entities",       result.entities_count       ?? stats.entities      ?? "—"],
    ["Relationships",  result.relationships_count  ?? stats.relationships ?? "—"],
    ["Graph nodes",    stats.nodes       ?? "—"],
    ["Graph edges",    stats.edges       ?? "—"],
    ["Communities",    stats.communities ?? "—"],
  ];
  grid.innerHTML = cells
    .map(([label, num]) =>
      `<div class="stat-box"><div class="num">${num}</div><div class="label">${label}</div></div>`
    )
    .join("");

  const errs = result.per_doc_errors || [];
  document.getElementById("errors-block").innerHTML = errs.length
    ? `<div class="error-note">${errs.length} doc(s) had extraction errors:<br>` +
      errs.map((e) => `• ${e.filename}: ${e.error}`).join("<br>") + `</div>`
    : "";

  const graphBtn = document.getElementById("view-graph");
  graphBtn.style.display = stats.nodes ? "" : "none";

  resultCard.classList.remove("hidden");
}

function resetRunBtn() {
  runBtn.disabled = false;
  runBtn.textContent = "Run data prep";
}

// ═══════════════════════════════════════════════════════════════
// ── TAB 2: Community Summariser ──────────────────────────────
// ═══════════════════════════════════════════════════════════════

let commJobId       = null;
let commPollTimer   = null;
let commMaxComm     = null;
let commTotalCount  = 0;

async function initCommunityTab() {
  const statusEl  = document.getElementById("comm-prereq-status");
  const runCard   = document.getElementById("comm-run-card");
  statusEl.innerHTML = '<span class="muted">Checking prerequisites…</span>';
  runCard.classList.add("hidden");

  try {
    const res  = await fetch("/api/community/prerequisites");
    const data = await res.json();
    renderCommPrereqs(data);
  } catch (err) {
    statusEl.innerHTML = `<span class="prereq-err">Error: ${err.message}</span>`;
  }
}

function renderCommPrereqs(data) {
  const statusEl = document.getElementById("comm-prereq-status");
  const runCard  = document.getElementById("comm-run-card");

  if (!data.ready) {
    statusEl.innerHTML =
      '<span class="prereq-warn">⚠ Data Prep has not completed yet. ' +
      'Run Tab 1 first to build the knowledge graph.</span>';
    return;
  }

  const done = data.summaries_done;
  const total = data.community_count;
  statusEl.innerHTML = done === total
    ? `<span class="prereq-ok">✓ ${total} communities ready · ${done} summaries already written</span>`
    : `<span class="prereq-ok">✓ ${total} communities ready</span>` +
      (done ? ` <span class="muted">(${done} summaries already written)</span>` : "");

  commTotalCount = total;
  commMaxComm    = null;

  // Community meta bar
  const metaEl = document.getElementById("comm-meta");
  metaEl.innerHTML = `
    <div><div class="big">${total}</div><div class="label">Communities</div></div>
    <div><div class="big">${done}</div><div class="label">Already summarised</div></div>
    <div><div class="big">${total - done}</div><div class="label">To process</div></div>
  `;

  // Batch buttons
  const batchEl = document.getElementById("comm-batch-options");
  batchEl.innerHTML = "";
  const thresholds = [3, 5, 10].filter((n) => n < total);
  thresholds.forEach((n) => batchEl.appendChild(makeCommBatchBtn(`First ${n}`, n)));
  const allBtn = makeCommBatchBtn(`All ${total}`, null);
  allBtn.classList.add("selected");
  batchEl.appendChild(allBtn);

  runCard.classList.remove("hidden");
}

function makeCommBatchBtn(label, max) {
  const btn = document.createElement("button");
  btn.className = "batch-btn";
  btn.textContent = label;
  btn.addEventListener("click", () => {
    document.querySelectorAll("#comm-batch-options .batch-btn")
      .forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
    commMaxComm = max;
  });
  return btn;
}

// ── Run ──────────────────────────────────────────────────────
document.getElementById("comm-run-btn").addEventListener("click", async () => {
  const commRunBtn = document.getElementById("comm-run-btn");
  commRunBtn.disabled = true;
  commRunBtn.textContent = "Running…";

  document.getElementById("comm-result-card").classList.add("hidden");
  document.getElementById("comm-partial-badge").classList.add("hidden");
  document.getElementById("comm-progress-card").classList.remove("hidden");
  document.getElementById("comm-log").textContent = "";
  document.getElementById("comm-stop-btn").disabled = false;
  setCommProgress(0, "Starting…");

  try {
    const res = await fetch("/api/community/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_communities: commMaxComm }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const data = await res.json();
    commJobId = data.job_id;
    pollCommStatus(commJobId);
  } catch (err) {
    appendCommLog("ERROR: " + err.message);
    resetCommBtn();
  }
});

// ── Stop ─────────────────────────────────────────────────────
document.getElementById("comm-stop-btn").addEventListener("click", async () => {
  if (!commJobId) return;
  const stopBtn = document.getElementById("comm-stop-btn");
  stopBtn.disabled = true;
  stopBtn.textContent = "Stopping…";
  try {
    await fetch(`/api/community/cancel/${commJobId}`, { method: "POST" });
    appendCommLog("Stop requested — finishing in-flight summaries…");
  } catch (err) {
    appendCommLog("Stop error: " + err.message);
  }
});

// ── Polling ──────────────────────────────────────────────────
function pollCommStatus(jobId) {
  clearInterval(commPollTimer);
  commPollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/community/status/${jobId}`);
      const job = await res.json();
      renderCommLogs(job.logs);
      setCommProgress(job.progress * 100, prettyCommStage(job.stage));
      if (job.status === "completed") {
        clearInterval(commPollTimer);
        renderCommResult(job.result);
        resetCommBtn();
        document.getElementById("comm-stop-btn").disabled = true;
      } else if (job.status === "failed") {
        clearInterval(commPollTimer);
        appendCommLog("FAILED: " + (job.error || "unknown error"));
        resetCommBtn();
        document.getElementById("comm-stop-btn").disabled = true;
      }
    } catch (err) {
      appendCommLog("Polling error: " + err.message);
    }
  }, 1500);
}

// ── Render helpers ───────────────────────────────────────────
function renderCommLogs(logs) {
  if (!logs?.length) return;
  const el = document.getElementById("comm-log");
  el.textContent = logs.map((l) => l.message).join("\n");
  el.scrollTop = el.scrollHeight;
}

function appendCommLog(msg) {
  const el = document.getElementById("comm-log");
  el.textContent += (el.textContent ? "\n" : "") + msg;
  el.scrollTop = el.scrollHeight;
}

function setCommProgress(pct, stage) {
  const p = Math.max(0, Math.min(100, pct));
  document.getElementById("comm-fill").style.width = p + "%";
  document.getElementById("comm-pct").textContent  = Math.round(p) + "%";
  if (stage) document.getElementById("comm-stage").textContent = stage;
}

function prettyCommStage(stage) {
  return {
    start:      "Starting…",
    validate:   "Checking prerequisites",
    summarise:  "Summarising communities with Gemini",
    cancelling: "Stopping — finishing in-flight summaries…",
    done:       "Complete",
    error:      "Error",
  }[stage] || stage || "";
}

async function renderCommResult(result) {
  if (!result) return;

  if (result.was_cancelled) {
    document.getElementById("comm-partial-badge").classList.remove("hidden");
  }

  const ok    = (result.results || []).filter((r) => !r.error).length;
  const errs  = (result.errors || []).length;
  document.getElementById("comm-stats").innerHTML =
    `<div class="scan-summary" style="margin:0 0 12px">
      <span class="count">${ok}</span>
      <span>summaries written</span>
      ${errs ? `<span class="prereq-warn">${errs} error(s)</span>` : ""}
      ${result.was_cancelled ? `<span class="muted">of ${commTotalCount} total</span>` : ""}
    </div>`;

  // Load summaries list
  try {
    const res  = await fetch("/api/community/summaries");
    const data = await res.json();
    const listEl = document.getElementById("comm-summary-list");
    listEl.innerHTML = "";
    (data.summaries || []).forEach((s) => {
      const item = document.createElement("div");
      item.className = "summary-item";
      // Extract community number from filename e.g. community_03.md → 3
      const num = s.file.replace("community_", "").replace(".md", "").replace(/^0+/, "") || "0";
      item.innerHTML = `
        <div class="summary-header">
          <span class="summary-comm-id">Community ${num}</span>
          <span class="summary-title">${escHtml(s.title)}</span>
          <span class="summary-chevron">▶</span>
        </div>
        <pre class="summary-body">${escHtml(s.preview)}${s.preview.length >= 400 ? "\n…" : ""}</pre>
      `;
      item.querySelector(".summary-header").addEventListener("click", () => {
        item.classList.toggle("open");
      });
      listEl.appendChild(item);
    });
  } catch (_) {}

  document.getElementById("comm-result-card").classList.remove("hidden");
}

function resetCommBtn() {
  const btn = document.getElementById("comm-run-btn");
  btn.disabled = false;
  btn.textContent = "Run community summariser";
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ═══════════════════════════════════════════════════════════════
// ── TAB 3: Query Agent ───────────────────────────────────────
// ═══════════════════════════════════════════════════════════════

async function initQueryTab() {
  const statusEl  = document.getElementById("query-prereq-status");
  const chatWrap  = document.getElementById("query-chat-wrap");
  statusEl.innerHTML = '<span class="muted">Checking prerequisites…</span>';
  chatWrap.classList.add("hidden");

  try {
    const res  = await fetch("/api/query/prerequisites");
    const data = await res.json();
    renderQueryPrereqs(data);
  } catch (err) {
    statusEl.innerHTML = `<span class="prereq-err">Error: ${err.message}</span>`;
  }
}

function renderQueryPrereqs(data) {
  const statusEl = document.getElementById("query-prereq-status");
  const chatWrap = document.getElementById("query-chat-wrap");

  if (!data.ready) {
    statusEl.innerHTML =
      '<span class="prereq-warn">⚠ Graph not ready. Complete Data Prep (Tab 1) first.</span>';
    return;
  }

  const warnSummary = data.summaries_warning
    ? ' <span class="prereq-warn">· Community summaries missing — global queries may be weak</span>'
    : "";
  statusEl.innerHTML =
    `<span class="prereq-ok">✓ Graph ready — ${data.entities} entities · ` +
    `${data.communities} communities · ${data.summaries} summaries</span>${warnSummary}`;

  chatWrap.classList.remove("hidden");
  loadSuggestions();
}

async function loadSuggestions() {
  try {
    const res  = await fetch("/api/query/suggestions");
    const data = await res.json();
    const el   = document.getElementById("query-chips");
    el.innerHTML = "";
    (data.suggestions || []).forEach((q) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = q;
      chip.addEventListener("click", () => submitQuery(q));
      el.appendChild(chip);
    });
  } catch (_) {}
}

// ── Input handlers ───────────────────────────────────────────
const queryInput   = document.getElementById("query-input");
const querySendBtn = document.getElementById("query-send-btn");

querySendBtn.addEventListener("click", () => {
  const q = queryInput.value.trim();
  if (q) submitQuery(q);
});

queryInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const q = queryInput.value.trim();
    if (q) submitQuery(q);
  }
});

// ── Submit query ─────────────────────────────────────────────
async function submitQuery(question) {
  queryInput.value = "";
  querySendBtn.disabled = true;

  const messagesEl = document.getElementById("query-messages");

  // User bubble
  const pair = document.createElement("div");
  pair.className = "msg-pair";
  pair.innerHTML = `<div class="msg-user">${escHtml(question)}</div>`;

  // Thinking bubble
  const thinking = document.createElement("div");
  thinking.className = "msg-thinking";
  thinking.textContent = "Searching graph and synthesising answer…";
  pair.appendChild(thinking);
  messagesEl.appendChild(pair);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  try {
    const res = await fetch("/api/query/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        query_type: document.getElementById("query-type").value,
        top_chunks: 4,
        hops: 1,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    thinking.replaceWith(renderAgentMsg(data));
  } catch (err) {
    thinking.className = "msg-agent";
    thinking.style.color = "var(--red)";
    thinking.textContent = "Error: " + err.message;
  } finally {
    querySendBtn.disabled = false;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

// ── Render agent answer ──────────────────────────────────────
function renderAgentMsg(data) {
  const el = document.createElement("div");
  el.className = "msg-agent";

  // Convert basic markdown to HTML for the answer
  const html = simpleMarkdown(data.answer || "_(no answer)_");
  el.innerHTML = html;

  // Meta pills
  const meta = document.createElement("div");
  meta.className = "meta";
  [
    `${data.query_type?.toUpperCase()} query`,
    `${data.entities_found} entities`,
    `${data.chunks_cited} chunks`,
    data.communities_used ? `${data.communities_used} communities` : null,
  ]
    .filter(Boolean)
    .forEach((label) => {
      const pill = document.createElement("span");
      pill.className = "meta-pill";
      pill.textContent = label;
      meta.appendChild(pill);
    });
  el.appendChild(meta);

  // Also-try chips
  if (data.also_try?.length) {
    const at = document.createElement("div");
    at.className = "also-try";
    at.innerHTML = "<span>Also try: </span>";
    data.also_try.forEach((q) => {
      const chip = document.createElement("span");
      chip.className = "also-chip";
      chip.textContent = q;
      chip.addEventListener("click", () => submitQuery(q));
      at.appendChild(chip);
    });
    el.appendChild(at);
  }

  return el;
}

// ── Minimal markdown → HTML ──────────────────────────────────
function simpleMarkdown(text) {
  return text
    // Bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Italic citations  *(source, p.N)*
    .replace(/\*\((.+?)\)\*/g, "<em>($1)</em>")
    // Inline code
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    // Table rows
    .replace(/^\|(.+)\|$/gm, (row) => {
      const cells = row.split("|").slice(1, -1);
      return "<tr>" + cells.map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>";
    })
    // Wrap consecutive <tr> lines in <table>
    .replace(/((?:<tr>.*<\/tr>\n?)+)/g, "<table>$1</table>")
    // Header rows (separator lines like | --- |)
    .replace(/<tr><td>[-: ]+<\/td>.*?<\/tr>/g, "")
    // Bullet points
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>")
    // Newlines → <br> (outside block elements)
    .replace(/\n/g, "<br>");
}
