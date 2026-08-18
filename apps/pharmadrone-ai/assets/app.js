"use strict";

const state = {
  token: localStorage.getItem("pharmadrone_ai_token") || "",
  user: null,
  conversationId: null,
  lastLeads: [],
  lastReport: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401) signOut();
    throw new Error(payload.detail || payload || "Request failed.");
  }
  return payload;
}

function signOut() {
  state.token = "";
  state.user = null;
  state.conversationId = null;
  localStorage.removeItem("pharmadrone_ai_token");
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
}

function setAuthMode(register) {
  $("#register-fields").hidden = !register;
  $("#login-tab").classList.toggle("active", !register);
  $("#register-tab").classList.toggle("active", register);
  $("#auth-submit").textContent = register ? "Create account" : "Log in";
  $("#auth-password").autocomplete = register ? "new-password" : "current-password";
  $("#auth-form").dataset.mode = register ? "register" : "login";
}

async function authenticate(event) {
  event.preventDefault();
  const register = event.currentTarget.dataset.mode === "register";
  const body = {
    email: $("#auth-email").value,
    password: $("#auth-password").value,
  };
  if (register) {
    body.display_name = $("#display-name").value;
    body.workspace_name = $("#workspace-name").value;
  }
  $("#auth-error").textContent = "";
  try {
    const result = await api(register ? "/api/auth/register" : "/api/auth/login", {
      method: "POST", body: JSON.stringify(body),
    });
    state.token = result.access_token;
    state.user = result.user;
    localStorage.setItem("pharmadrone_ai_token", state.token);
    await enterApp();
  } catch (error) {
    $("#auth-error").textContent = error.message;
  }
}

async function enterApp() {
  if (!state.user) state.user = await api("/api/auth/me");
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  $("#workspace-label").textContent = state.user.workspace_name;
  await loadConversations();
}

function showView(name) {
  $$(".view").forEach((view) => view.classList.remove("active-view"));
  $$(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $(`#${name}-view`).classList.add("active-view");
  if (name === "saved-leads") loadSavedLeads();
  if (name === "saved-reports") loadSavedReports();
  if (name === "account") loadAccount();
}

function addMessage(role, text) {
  $("#welcome")?.remove();
  const message = document.createElement("article");
  message.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "assistant" ? "PD" : "You";
  const body = document.createElement("div");
  body.className = "message-body";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  body.append(paragraph);
  message.append(avatar, body);
  $("#chat-stream").append(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
  return body;
}

function statusClass(status) {
  return status === "Pitch-ready draft" ? "status pitch-ready" : "status";
}

function sourceLinks(links) {
  const container = document.createElement("div");
  container.className = "source-list";
  (links || []).slice(0, 12).forEach((source) => {
    const anchor = document.createElement("a");
    anchor.className = "source-link";
    anchor.href = source.url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.textContent = source.title || "Evidence source";
    container.append(anchor);
  });
  return container;
}

function limitations(items) {
  const block = document.createElement("div");
  block.className = "limitations";
  block.textContent = (items || []).join(" ") || "Evidence is incomplete and requires validation.";
  return block;
}

function leadCard(lead, allowSave = true) {
  const card = document.createElement("article");
  card.className = "lead-card";
  const header = document.createElement("header");
  const heading = document.createElement("h3");
  heading.textContent = `${lead.target_company} — ${lead.theme}`;
  const badge = document.createElement("span");
  badge.className = statusClass(lead.readiness_status);
  badge.textContent = lead.readiness_status;
  header.append(heading, badge);
  const hypothesis = document.createElement("p");
  hypothesis.textContent = lead.opportunity_hypothesis;
  const angle = document.createElement("p");
  angle.textContent = `Pitch angle: ${lead.pitch_angle}`;
  const evidence = document.createElement("p");
  evidence.textContent = `Evidence summary: ${lead.evidence_summary || "Evidence is incomplete and requires validation."}`;
  card.append(header, hypothesis, angle, evidence, sourceLinks(lead.source_links), limitations(lead.limitations));
  if (allowSave) {
    const actions = document.createElement("div");
    actions.className = "actions";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save lead";
    save.addEventListener("click", async () => {
      try {
        await api("/api/ai/save-lead", { method: "POST", body: JSON.stringify({ lead }) });
        save.textContent = "Saved";
        save.disabled = true;
      } catch (error) {
        save.textContent = `Save failed: ${error.message}`;
      }
    });
    actions.append(save);
    card.append(actions);
  }
  return card;
}

function reportCard(report, allowSave = true) {
  const card = document.createElement("article");
  card.className = "report-card";
  const header = document.createElement("header");
  const heading = document.createElement("h3");
  heading.textContent = report.report_title;
  const badge = document.createElement("span");
  badge.className = statusClass(report.readiness_status);
  badge.textContent = report.readiness_status;
  header.append(heading, badge);
  const summary = document.createElement("p");
  summary.textContent = report.executive_summary;
  const preview = document.createElement("pre");
  preview.className = "report-preview";
  preview.textContent = report.markdown_report;
  const actions = document.createElement("div");
  actions.className = "actions";
  if (allowSave) {
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save report";
    save.addEventListener("click", async () => {
      try {
        await api("/api/ai/save-report", { method: "POST", body: JSON.stringify({ report }) });
        save.textContent = "Saved";
        save.disabled = true;
      } catch (error) {
        save.textContent = `Save failed: ${error.message}`;
      }
    });
    actions.append(save);
  }
  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Export Markdown";
  download.addEventListener("click", async () => {
    try {
      await exportReport(report);
    } catch (error) {
      download.textContent = `Export failed: ${error.message}`;
    }
  });
  actions.append(download);
  card.append(header, summary, sourceLinks(report.source_table?.map((row) => ({
    title: row.title, url: row.source_url,
  })).filter((row) => row.url)), limitations(report.limitations), actions, preview);
  return card;
}

async function exportReport(report) {
  const response = await fetch("/api/ai/export-report", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
    body: JSON.stringify({ report }),
  });
  if (!response.ok) throw new Error("Report export failed.");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${report.report_title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function sendPrompt(prompt) {
  showView("chat");
  addMessage("user", prompt);
  const body = addMessage("assistant", "Querying retained PharmaDrone intelligence…");
  body.classList.add("loading");
  $("#send-button").disabled = true;
  try {
    const result = await api("/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ prompt, conversation_id: state.conversationId, use_llm: false }),
    });
    state.conversationId = result.conversation_id;
    body.classList.remove("loading");
    body.textContent = "";
    const paragraph = document.createElement("p");
    paragraph.textContent = result.message;
    body.append(paragraph);
    const data = result.result?.data;
    if (Array.isArray(data)) {
      state.lastLeads = data;
      const grid = document.createElement("div");
      grid.className = "lead-grid";
      data.forEach((lead) => grid.append(leadCard(lead)));
      body.append(grid);
    } else if (data?.markdown_report) {
      state.lastReport = data;
      body.append(reportCard(data));
    } else if (result.result) {
      body.append(sourceLinks(result.result.source_links), limitations(result.result.limitations));
    }
    await loadConversations();
  } catch (error) {
    body.classList.remove("loading");
    body.textContent = `Unable to complete this request: ${error.message}`;
  } finally {
    $("#send-button").disabled = false;
  }
}

async function loadConversations() {
  const result = await api("/api/ai/conversations");
  const list = $("#conversation-list");
  list.textContent = "";
  result.data.forEach((conversation) => {
    const button = document.createElement("button");
    button.className = "conversation-item";
    button.type = "button";
    button.textContent = conversation.title;
    button.addEventListener("click", async () => {
      state.conversationId = conversation.conversation_id;
      showView("chat");
      const messages = await api(`/api/ai/conversations/${conversation.conversation_id}/messages`);
      $("#chat-stream").textContent = "";
      messages.data.forEach((message) => {
        const text = message.role === "user" ? message.content.text : message.content.message;
        addMessage(message.role, text || "Saved tool response");
      });
    });
    list.append(button);
  });
}

async function loadSavedLeads() {
  const container = $("#saved-leads-list");
  container.innerHTML = '<p class="loading">Loading saved leads…</p>';
  const result = await api("/api/ai/saved-leads");
  container.textContent = "";
  if (!result.data.length) container.innerHTML = '<p class="empty">No saved leads yet.</p>';
  result.data.forEach((lead) => {
    const card = leadCard(lead, false);
    const actions = document.createElement("div");
    actions.className = "actions";
    const remove = document.createElement("button");
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      await api(`/api/ai/saved-leads/${lead.saved_id}`, { method: "DELETE" });
      card.remove();
    });
    actions.append(remove);
    card.append(actions);
    container.append(card);
  });
}

async function loadSavedReports() {
  const container = $("#saved-reports-list");
  container.innerHTML = '<p class="loading">Loading saved reports…</p>';
  const result = await api("/api/ai/saved-reports");
  container.textContent = "";
  if (!result.data.length) container.innerHTML = '<p class="empty">No saved reports yet.</p>';
  result.data.forEach((report) => {
    const card = reportCard(report, false);
    const remove = document.createElement("button");
    remove.textContent = "Delete saved report";
    remove.addEventListener("click", async () => {
      await api(`/api/ai/saved-reports/${report.saved_id}`, { method: "DELETE" });
      card.remove();
    });
    card.querySelector(".actions").append(remove);
    container.append(card);
  });
}

async function loadAccount() {
  const billing = await api("/api/billing/status");
  const container = $("#account-details");
  container.textContent = "";
  const heading = document.createElement("h2");
  heading.textContent = state.user.workspace_name;
  const details = document.createElement("p");
  details.textContent = `${state.user.display_name} · ${state.user.email} · ${billing.plan}`;
  const usage = document.createElement("pre");
  usage.className = "report-preview";
  usage.textContent = JSON.stringify(billing.usage, null, 2);
  const note = document.createElement("p");
  note.textContent = billing.billing_status;
  container.append(heading, details, usage, note);
}

function newChat() {
  state.conversationId = null;
  $("#chat-stream").innerHTML = '<section id="welcome" class="welcome"><div class="brand-mark hero">PD</div><h2>What opportunity should we investigate?</h2><p>Start a new evidence-grounded conversation below.</p></section>';
  showView("chat");
  $("#prompt-input").focus();
}

$("#login-tab").addEventListener("click", () => setAuthMode(false));
$("#register-tab").addEventListener("click", () => setAuthMode(true));
$("#auth-form").addEventListener("submit", authenticate);
$("#logout").addEventListener("click", signOut);
$("#new-chat").addEventListener("click", newChat);
$$('.nav-button').forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
$("#prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("#prompt-input");
  const prompt = input.value.trim();
  if (!prompt) return;
  input.value = "";
  sendPrompt(prompt);
});
$$('#starter-prompts button').forEach((button) => button.addEventListener("click", () => sendPrompt(button.textContent)));

setAuthMode(false);
if (state.token) {
  enterApp().catch(signOut);
}
