const chatScroll = document.getElementById("chat-scroll");
const typingNote = document.getElementById("typing-note");
const fileInput = document.getElementById("file-input");
const uploadBox = document.getElementById("upload-box");
const uploadFilenameEl = document.getElementById("upload-filename");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

// 이모지 대신 쓰는 SVG 아이콘 — OS/브라우저별로 이모지 폰트가 깨져 보이는 걸 피하려고 통일
const ICONS = {
  factory: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18"/><path d="M5 21V10l4 3V10l4 3V10l4 3v8"/><path d="M17 21V7l3 2v12"/><path d="M9 21v-4h2v4"/></svg>`,
  warning: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  clipboard: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="15" y2="16"/></svg>`,
  search: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  listCheck: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 7 5 9 9 5"/><line x1="13" y1="6" x2="21" y2="6"/><polyline points="3 15 5 17 9 13"/><line x1="13" y1="14" x2="21" y2="14"/></svg>`,
  cpu: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>`,
};

let currentUser = null;
let lastUploadedName = null;

function escapeHtml(str) {
  return (str ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function scrollToBottom() {
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function renderTextMessage(role, text) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  const avatar = role === "assistant" ? `<div class="msg-avatar">${ICONS.factory}</div>` : "";
  const bubble = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
  row.innerHTML = role === "assistant" ? avatar + bubble : bubble;
  chatScroll.appendChild(row);
}

// 규칙 대조 결과(위반위험/기준이내/미기재)와 AI 점검 결과(present=true→기준이내, false/null→미기재)를
// 하나의 기준(상태)으로 합쳐 "위반위험 / 기준이내 / 미기재" 3개 토글로만 보여준다.
const BUCKET_DEFS = [
  { key: "violation", dot: "dot-danger", label: "위반 위험", cls: "sev-danger" },
  { key: "ok", dot: "dot-ok", label: "기준 이내", cls: "sev-ok" },
  { key: "missing", dot: "dot-warn", label: "미기재", cls: "sev-warn" },
];

function bucketize(ruleResults, aiResults, aiAvailable) {
  const buckets = { violation: [], ok: [], missing: [] };
  for (const r of ruleResults) {
    const key = r.status === "위반 위험" ? "violation" : r.status === "기준 이내" ? "ok" : "missing";
    buckets[key].push({ kind: "rule", data: r });
  }
  if (aiAvailable) {
    for (const item of aiResults) {
      const key = item.present === true ? "ok" : "missing";
      buckets[key].push({ kind: "ai", data: item });
    }
  }
  return buckets;
}

function groupRuleEntries(entries) {
  const order = [];
  const map = new Map();
  for (const e of entries) {
    const name = e.data.item_name;
    if (!map.has(name)) {
      map.set(name, []);
      order.push(name);
    }
    map.get(name).push(e.data);
  }
  return order.map((name) => ({ name, rows: map.get(name) }));
}

function ruleSubRowHtml(r) {
  const valueStr = r.extracted_value !== null && r.extracted_value !== undefined
    ? `${r.extracted_value} ${r.unit}`
    : "미기재";
  return `
    <div class="group-sub-row plain">
      <div class="sub-body">
        <div class="sub-line"><span class="sub-value">${escapeHtml(valueStr)}</span><span class="sub-limit">기준 ${r.comparison} ${r.limit_value} ${escapeHtml(r.unit)}</span></div>
        ${r.note ? `<div class="sub-note">${escapeHtml(r.note)}</div>` : ""}
      </div>
    </div>`;
}

function ruleGroupBlockHtml(group) {
  const multi = group.rows.length > 1;
  return `
    <div class="group-block">
      <div class="group-header">
        <span class="group-title">${escapeHtml(group.name)}</span>
        ${multi ? `<span class="group-count">기준 ${group.rows.length}건 대조</span>` : ""}
      </div>
      <div class="group-sub-list">${group.rows.map(ruleSubRowHtml).join("")}</div>
    </div>`;
}

function aiRowHtml(item) {
  return `
    <div class="result-row">
      <div class="result-body">
        <div class="rb-title">${escapeHtml(item.item)}</div>
        <div class="rb-detail">${escapeHtml(item.reason)}</div>
      </div>
    </div>`;
}

function bucketBodyHtml(entries) {
  if (!entries.length) return `<div class="bucket-empty">해당하는 항목이 없습니다.</div>`;

  const ruleEntries = entries.filter((e) => e.kind === "rule");
  const aiEntries = entries.filter((e) => e.kind === "ai");
  let html = "";

  if (ruleEntries.length) {
    const groups = groupRuleEntries(ruleEntries);
    html += `<div class="bucket-subgroup-label">${ICONS.listCheck}<span>규칙 기반 대조 (${ruleEntries.length}건)</span></div>`;
    html += groups.map(ruleGroupBlockHtml).join("");
  }
  if (aiEntries.length) {
    html += `<div class="bucket-subgroup-label">${ICONS.cpu}<span>AI 보조 점검 (${aiEntries.length}건)</span></div>`;
    html += aiEntries.map((e) => aiRowHtml(e.data)).join("");
  }
  return html;
}

function renderUploadMessage(upload) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";

  const ruleResults = upload.rule_results || [];
  const aiResults = upload.ai_results || [];
  const aiAvailable = upload.ai_results_available;
  const buckets = bucketize(ruleResults, aiResults, aiAvailable);

  const sectionsHtml = BUCKET_DEFS.map(({ key, dot, label, cls }) => {
    const entries = buckets[key];
    return `
      <details class="uc-section ${cls}">
        <summary><span><span class="dot ${dot}"></span>${label}</span><span class="uc-count">${entries.length}건</span></summary>
        <div class="uc-section-body">${bucketBodyHtml(entries)}</div>
      </details>`;
  }).join("");

  const aiKeyNote = !aiAvailable
    ? `<div class="uc-ragnote">${ICONS.warning}<span>OpenAI API 키가 없어 AI 보조 점검(중대재해처벌법 등 문맥 판단 항목)은 건너뛰고 규칙 기반 대조 결과만 표시했습니다.</span></div>`
    : "";

  const ragNoteIcon = upload.rag_note_status === "warn" ? ICONS.warning : ICONS.search;

  row.innerHTML = `
    <div class="msg-avatar">${ICONS.factory}</div>
    <div class="upload-card">
      <div class="uc-title">${ICONS.clipboard}<span>검토 결과 — ${escapeHtml(upload.filename)}</span></div>
      <div class="uc-meta">텍스트 추출 ${upload.text_length}자 · 발전소: ${escapeHtml(upload.plant)}</div>
      ${sectionsHtml}
      ${aiKeyNote}
      ${upload.rag_note ? `<div class="uc-ragnote">${ragNoteIcon}<span>${escapeHtml(upload.rag_note)}</span></div>` : ""}
    </div>`;
  chatScroll.appendChild(row);
}

function renderMessages(messages) {
  chatScroll.innerHTML = "";
  for (const m of messages) {
    if (m.kind === "upload") renderUploadMessage(m.upload);
    else renderTextMessage(m.role, m.text || "");
  }
  scrollToBottom();
}

// ── 초기 로드 ─────────────────────────────────
fetch("/api/me", { credentials: "same-origin" })
  .then((res) => {
    if (!res.ok) {
      window.location.href = "/app/login.html";
      throw new Error("not logged in");
    }
    return res.json();
  })
  .then((user) => {
    currentUser = user;
    document.getElementById("plant-chip").innerHTML = `${ICONS.factory}<span>${escapeHtml(user.plant_name)}</span>`;
    document.getElementById("plant-scope-note").textContent =
      `이 발전소(${user.plant_name})의 배출기준 + BOD·중대재해처벌법처럼 발전소에 국한되지 않는 공통 기준을 함께 대조합니다.`;
    return fetch("/api/messages", { credentials: "same-origin" });
  })
  .then((res) => res.json())
  .then((data) => renderMessages(data.messages || []))
  .catch(() => {});

// ── 네비게이션 ─────────────────────────────────
document.getElementById("guide-btn").addEventListener("click", () => {
  window.location.href = "/app/guide.html";
});
document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/app/login.html";
});

// ── 파일 업로드 ────────────────────────────────
uploadBox.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file || file.name === lastUploadedName) return;
  lastUploadedName = file.name;
  uploadFilenameEl.textContent = `선택됨: ${file.name}`;

  const formData = new FormData();
  formData.append("file", file);

  typingNote.classList.remove("hidden");
  typingNote.innerHTML = `<span class="spinner"></span> 서류를 분석하는 중...`;

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      credentials: "same-origin",
      body: formData,
    });
    const data = await res.json();
    renderMessages(data.messages || []);
  } catch (e) {
    renderTextMessage("assistant", "업로드 처리 중 오류가 발생했어요.");
    scrollToBottom();
  } finally {
    typingNote.classList.add("hidden");
    fileInput.value = "";
  }
});

// ── 채팅 전송 ─────────────────────────────────
async function sendQuestion() {
  const question = chatInput.value.trim();
  if (!question) return;
  chatInput.value = "";
  renderTextMessage("user", question);
  scrollToBottom();

  typingNote.classList.remove("hidden");
  typingNote.innerHTML = `<span class="spinner"></span> 벡터DB에서 관련 내용을 검색하는 중...`;
  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    if (res.ok) {
      renderTextMessage("assistant", data.reply);
    } else {
      renderTextMessage("assistant", data.detail || "오류가 발생했어요.");
    }
  } catch (e) {
    renderTextMessage("assistant", "채팅 처리 중 오류가 발생했어요.");
  } finally {
    typingNote.classList.add("hidden");
    sendBtn.disabled = false;
    scrollToBottom();
  }
}

sendBtn.addEventListener("click", sendQuestion);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendQuestion();
});
