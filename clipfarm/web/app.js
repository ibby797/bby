/* ClipFarm frontend — vanilla JS, polls the JSON API. */

const $ = (sel) => document.querySelector(sel);
let currentJobId = null;
let pollTimer = null;
let campaigns = [];

const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  return res.json();
};

const fmtTime = (s) => {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
};

/* ---------------------------------------------------------------- status */

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    $("#status-badges").innerHTML = [
      badge("ffmpeg", s.ffmpeg),
      badge("AI re-rank", s.ai_rerank),
      badge("TikTok API", s.tiktok_configured),
      badge(`account ${s.tiktok_connected ? "connected" : "not connected"}`, s.tiktok_connected),
      `<span class="badge">${s.posts_per_day} posts/day · ${s.post_mode} mode</span>`,
    ].join("");
    $("#tiktok-hint").textContent = s.tiktok_configured
      ? (s.tiktok_connected
          ? `Posting via official Content Posting API in "${s.post_mode}" mode. Unaudited apps: direct posts are private-only; inbox mode drops clips into your TikTok drafts to tap-post.`
          : "TikTok app configured — click Connect TikTok to authorize your account.")
      : "No TikTok API credentials set — automation runs in export mode: clips are marked ready on schedule and you post them manually. Set TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET to enable API posting.";
  } catch { /* server starting */ }
}
const badge = (label, on) => `<span class="badge ${on ? "on" : ""}">${on ? "●" : "○"} ${label}</span>`;

/* ------------------------------------------------------------------ jobs */

async function analyze() {
  const url = $("#url-input").value.trim();
  if (!url) return alert("Paste a YouTube URL first");
  try {
    const job = await api("/api/jobs", { method: "POST", body: { url } });
    watchJob(job.id);
  } catch (e) { alert(e.message); }
}

async function autopilot() {
  const url = $("#url-input").value.trim();
  if (!url) return alert("Paste a YouTube URL first");
  const clips = parseInt($("#clip-count").value) || 100;
  try {
    const res = await api("/api/autopilot", {
      method: "POST",
      body: { url, clips, mode: $("#render-mode").value, watermark: $("#watermark").value },
    });
    watchJob(res.job_id);
  } catch (e) { alert(e.message); }
}

function watchJob(jobId) {
  currentJobId = jobId;
  $("#job-progress").classList.remove("hidden");
  clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 2000);
  pollJob();
}

async function pollJob() {
  if (!currentJobId) return;
  try {
    const job = await api(`/api/jobs/${currentJobId}`);
    $("#job-progress").textContent =
      `${job.title || job.url} — ${job.status}${job.progress ? " · " + job.progress : ""}` +
      (job.error ? ` · ERROR: ${job.error}` : "");
    if (job.moments && job.moments.length) renderMoments(job);
    if (job.clips && job.clips.length) renderClips(job);
    if (["done", "error", "ready"].includes(job.status) && job.status !== "rendering") {
      if (job.status === "error") clearInterval(pollTimer);
    }
    refreshQueue();
  } catch { /* ignore transient */ }
}

/* --------------------------------------------------------------- moments */

function renderMoments(job) {
  $("#moments-card").classList.remove("hidden");
  $("#moments-count").textContent = `${job.moments.length} found · ${job.title || ""}`;
  $("#moments-list").innerHTML = job.moments.map((m, i) => `
    <div class="moment">
      <input type="checkbox" data-idx="${i}" ${i < 20 ? "checked" : ""}>
      <div class="score ${m.score >= 60 ? "" : m.score >= 40 ? "mid" : "low"}">${Math.round(m.score)}</div>
      <div class="body">
        <div class="time">${fmtTime(m.start)} → ${fmtTime(m.end)} (${Math.round(m.duration)}s)</div>
        <div class="hook">${escapeHtml(m.hook_text || "")}</div>
        <div class="text">${escapeHtml(m.text || "")}</div>
        <div class="reasons">${(m.reasons || []).map(r => `<span>${escapeHtml(r)}</span>`).join("")}</div>
      </div>
    </div>`).join("");
}

async function renderSelected() {
  const indices = [...document.querySelectorAll("#moments-list input:checked")]
    .map(cb => parseInt(cb.dataset.idx));
  if (!indices.length) return alert("Select at least one moment");
  try {
    await api(`/api/jobs/${currentJobId}/render`, {
      method: "POST",
      body: {
        moment_indices: indices,
        mode: $("#render-mode").value,
        watermark: $("#watermark").value,
      },
    });
    watchJob(currentJobId);
  } catch (e) { alert(e.message); }
}

/* ----------------------------------------------------------------- clips */

function renderClips(job) {
  $("#clips-card").classList.remove("hidden");
  const ok = job.clips.filter(c => c.ok);
  $("#clips-count").textContent = `${ok.length}/${job.clips.length} rendered`;
  $("#clips-list").innerHTML = job.clips.map(c => c.ok ? `
    <div class="clip">
      <video src="/api/jobs/${job.id}/clips/${c.file}" controls preload="none"></video>
      <div class="meta"><span>score ${Math.round(c.score)}</span><span>${Math.round(c.duration)}s</span></div>
      <div>${escapeHtml(c.hook || "")}</div>
    </div>` : `
    <div class="clip failed">✗ ${escapeHtml(c.file)}<br>${escapeHtml(c.error || "render failed")}</div>`
  ).join("");
}

async function queueAll() {
  try {
    const res = await api(`/api/jobs/${currentJobId}/queue`, {
      method: "POST", body: { campaign_id: campaigns[0]?.id || "" },
    });
    alert(`Queued ${res.queued} clips${res.skipped.length ? `, skipped ${res.skipped.length} (campaign rules)` : ""}`);
    refreshQueue();
  } catch (e) { alert(e.message); }
}

async function exportManifest() {
  try {
    const res = await api(`/api/jobs/${currentJobId}/manifest`, {
      method: "POST", body: { campaign_id: campaigns[0]?.id || "" },
    });
    window.open(res.csv, "_blank");
  } catch (e) { alert(e.message); }
}

/* ----------------------------------------------------------------- queue */

async function refreshQueue() {
  try {
    const q = await api("/api/queue");
    $("#queue-status").textContent =
      `${q.enabled ? "▶ running" : "⏸ paused"} · ${q.items.filter(i => i.status === "queued").length} queued · ` +
      `${q.items.filter(i => i.status === "posted").length} posted`;
    $("#queue-table tbody").innerHTML = q.items.slice(-60).reverse().map(i => `
      <tr>
        <td>${escapeHtml(i.clip_file)}</td>
        <td>${escapeHtml((i.caption || "").slice(0, 60))}</td>
        <td>${i.scheduled_at ? new Date(i.scheduled_at * 1000).toLocaleString() : "—"}</td>
        <td class="status-${i.status}">${i.status}${i.error ? " · " + escapeHtml(i.error.slice(0, 80)) : ""}</td>
        <td><button data-remove="${i.id}">✕</button></td>
      </tr>`).join("");
  } catch { /* ignore */ }
}

/* ------------------------------------------------------------- campaigns */

async function loadCampaigns() {
  try {
    campaigns = await api("/api/campaigns");
    const c = campaigns[0];
    if (!c) return;
    $("#c-name").value = c.name;
    $("#c-rate").value = c.rate_per_1k_views;
    $("#c-min").value = c.min_duration_s;
    $("#c-max").value = c.max_duration_s;
    $("#c-caption").value = c.caption_template;
    $("#c-mention").value = c.required_mention;
  } catch { /* ignore */ }
}

async function saveCampaign() {
  const body = {
    id: campaigns[0]?.id || "",
    name: $("#c-name").value,
    rate_per_1k_views: parseFloat($("#c-rate").value) || 1,
    min_duration_s: parseFloat($("#c-min").value) || 10,
    max_duration_s: parseFloat($("#c-max").value) || 60,
    caption_template: $("#c-caption").value || "{hook}",
    required_mention: $("#c-mention").value,
  };
  try {
    await api("/api/campaigns", { method: "POST", body });
    await loadCampaigns();
    alert("Campaign saved");
  } catch (e) { alert(e.message); }
}

/* --------------------------------------------------------------- history */

async function loadJobs() {
  try {
    const jobs = await api("/api/jobs");
    $("#jobs-list").innerHTML = jobs.map(j => `
      <div class="job-row" data-job="${j.id}">
        <span>${escapeHtml(j.title || j.url)}</span>
        <span class="muted">${j.status} · ${j.moments} moments · ${j.clips} clips</span>
      </div>`).join("") || '<p class="hint">No jobs yet.</p>';
  } catch { /* ignore */ }
}

/* ------------------------------------------------------------------ util */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------------------------ wire */

$("#analyze-btn").addEventListener("click", analyze);
$("#autopilot-btn").addEventListener("click", autopilot);
$("#render-top-btn").addEventListener("click", renderSelected);
$("#select-all-btn").addEventListener("click", () =>
  document.querySelectorAll("#moments-list input").forEach(cb => (cb.checked = true)));
$("#select-none-btn").addEventListener("click", () =>
  document.querySelectorAll("#moments-list input").forEach(cb => (cb.checked = false)));
$("#queue-all-btn").addEventListener("click", queueAll);
$("#manifest-btn").addEventListener("click", exportManifest);
$("#queue-toggle-btn").addEventListener("click", async () => {
  await api("/api/queue/toggle", { method: "POST" }); refreshQueue();
});
$("#campaign-save-btn").addEventListener("click", saveCampaign);
$("#queue-table").addEventListener("click", async (e) => {
  const id = e.target.dataset.remove;
  if (id) { await api(`/api/queue/${id}`, { method: "DELETE" }); refreshQueue(); }
});
$("#jobs-list").addEventListener("click", (e) => {
  const row = e.target.closest(".job-row");
  if (row) watchJob(row.dataset.job);
});

refreshStatus();
loadCampaigns();
loadJobs();
refreshQueue();
setInterval(refreshStatus, 15000);
setInterval(loadJobs, 10000);
