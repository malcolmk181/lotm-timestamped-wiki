"use strict";

// ---- state ---------------------------------------------------------------
let facts = [];
let entities = [];
let summaries = [];
let meta = { max_chapter: 1 };
let chapter = 1;
let view = "about";
let selectedId = null;

const byId = (arr) => Object.fromEntries(arr.map((e) => [e.id, e]));
let entityMap = {};
let factMap = {};
let entityNameMap = {}; // lowercased name/alias -> entity id (visible at this chapter)

// ---- temporal predicates -------------------------------------------------
const isActiveAt = (f, ch) =>
  f.established_at <= ch && (f.revoked_at === null || f.revoked_at > ch);

const isVisibleAt = (e, ch) => e.first_seen <= ch;

const certaintyLabel = {
  fact: "fact",
  inference: "inference",
  hypothesis: "hypothesis",
  speculation: "speculation",
};

// ---- entity linking ------------------------------------------------------
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function rebuildEntityLinks() {
  entityNameMap = {};
  for (const e of entities) {
    if (!isVisibleAt(e, chapter)) continue;
    entityNameMap[e.name.toLowerCase()] = e.id;
    for (const a of e.aliases || []) entityNameMap[a.toLowerCase()] = e.id;
  }
}

function linkify(text) {
  const names = Object.keys(entityNameMap).sort((a, b) => b.length - a.length);
  if (!names.length) return escapeHtml(text);
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp("\\b(" + names.map(esc).join("|") + ")\\b", "gi");
  return String(text).split(re).map((part, i) => {
    if (i % 2 === 1) {
      const id = entityNameMap[part.toLowerCase()];
      return `<a class="entity-link" data-entity="${escapeHtml(id)}" href="#">${escapeHtml(part)}</a>`;
    }
    return escapeHtml(part);
  }).join("");
}

// ---- loading -------------------------------------------------------------
async function load(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`failed to load ${path}: ${res.status}`);
  return res.json();
}

async function init() {
  [facts, entities, summaries, meta] = await Promise.all([
    load("data/facts.json"),
    load("data/entities.json"),
    load("data/summaries.json"),
    load("data/meta.json"),
  ]);
  factMap = byId(facts);
  entityMap = byId(entities);

  chapter = meta.max_chapter;
  const slider = document.getElementById("chapter-slider");
  slider.max = meta.max_chapter;
  slider.value = chapter;

  slider.addEventListener("input", (e) => {
    chapter = Number(e.target.value);
    document.getElementById("chapter-value").textContent =
      `${chapter} / ${meta.max_chapter}`;
    render();
  });

  document.querySelectorAll(".view-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      view = btn.dataset.view;
      document.querySelectorAll(".view-tab").forEach((b) =>
        b.classList.toggle("active", b === btn));
      selectedId = null;
      render();
    });
  });

  document.addEventListener("click", (ev) => {
    const link = ev.target.closest(".entity-link");
    if (!link) return;
    ev.preventDefault();
    const id = link.dataset.entity;
    if (!id || !entityMap[id]) return;
    selectedId = id;
    view = "entities";
    document.querySelectorAll(".view-tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === "entities"));
    render();
  });

  render();
}

// ---- rendering -----------------------------------------------------------
function render() {
  rebuildEntityLinks();
  updateTopbar();
  const sidebar = document.getElementById("sidebar");
  const content = document.getElementById("content");
  const footer = document.getElementById("footer");
  const layout = document.querySelector(".layout");
  layout.classList.toggle("no-sidebar", view !== "entities");
  footer.textContent =
    `${meta.entity_count} entities · ${meta.fact_count} facts · ` +
    `${meta.summary_count} descriptions · showing world as of chapter ${chapter}`;

  if (view === "about") {
    renderAbout(sidebar, content);
  } else if (view === "entities") {
    renderEntityIndex(sidebar);
    renderEntityDetail(content);
  } else {
    renderChanges(sidebar, content);
  }
}

function updateTopbar() {
  document.getElementById("chapter-value").textContent =
    `${chapter} / ${meta.max_chapter}`;
  const link = document.getElementById("chapter-link");
  if (!link) return;
  const title = meta.chapters ? meta.chapters[String(chapter)] : null;
  link.textContent = title
    ? `Read ch ${chapter}: ${title} ↗`
    : `Read ch ${chapter} ↗`;
  link.href = `${meta.read_url_base}/${chapter}/`;
}

function activeEntityFacts(eid) {
  return facts.filter(
    (f) => f.entities.includes(eid) && isActiveAt(f, chapter));
}

function entityCount(eid) {
  return activeEntityFacts(eid).length;
}

function currentSummary(eid) {
  const cands = summaries.filter(
    (s) => s.entity === eid && s.established_at <= chapter &&
      (s.revoked_at === null || s.revoked_at > chapter));
  cands.sort((a, b) => b.established_at - a.established_at);
  return cands[0] || null;
}

function priorSummaries(eid) {
  return summaries
    .filter((s) => s.entity === eid && s.revoked_at !== null &&
      s.revoked_at <= chapter && s.established_at <= chapter)
    .sort((a, b) => a.established_at - b.established_at);
}

function appendProse(parent, text, className) {
  const parts = String(text).split(/\n+/).filter((p) => p.trim());
  for (const part of parts) {
    const p = document.createElement("p");
    p.className = className;
    p.innerHTML = linkify(part.trim());
    parent.appendChild(p);
  }
}

function renderEntityIndex(sidebar) {
  const visible = entities
    .filter((e) => isVisibleAt(e, chapter))
    .sort((a, b) => a.name.localeCompare(b.name));

  const groups = {};
  for (const e of visible) {
    (groups[e.type] ||= []).push(e);
  }

  sidebar.innerHTML = "";
  for (const type of Object.keys(groups).sort()) {
    const h = document.createElement("h3");
    h.className = "entity-group";
    h.textContent = type;
    sidebar.appendChild(h);

    for (const e of groups[type].sort((a, b) => a.name.localeCompare(b.name))) {
      const el = document.createElement("button");
      el.className =
        "entity-item" + (e.id === selectedId ? " selected" : "");
      const name = document.createElement("span");
      name.className = "entity-name";
      name.textContent = e.name;
      const badge = document.createElement("span");
      badge.className = "entity-badge";
      badge.textContent = entityCount(e.id);
      el.appendChild(name);
      el.appendChild(badge);
      el.addEventListener("click", () => {
        selectedId = e.id;
        renderEntityIndex(sidebar);
        renderEntityDetail(document.getElementById("content"));
      });
      sidebar.appendChild(el);
    }
  }

  if (visible.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No entities known yet at this chapter.";
    sidebar.appendChild(p);
  }
}

function renderEntityDetail(content) {
  content.innerHTML = "";
  if (!selectedId || !isVisibleAt(entityMap[selectedId], chapter)) {
    const p = document.createElement("p");
    p.className = "empty hint";
    p.textContent = "Select an entity from the list to explore its facts.";
    content.appendChild(p);
    return;
  }

  const e = entityMap[selectedId];

  const head = document.createElement("div");
  head.className = "entity-head";
  const h2 = document.createElement("h2");
  h2.textContent = e.name;
  head.appendChild(h2);
  const type = document.createElement("span");
  type.className = "entity-type";
  type.textContent = e.type;
  head.appendChild(type);
  content.appendChild(head);

  if (e.aliases && e.aliases.length) {
    const alias = document.createElement("p");
    alias.className = "entity-aliases";
    alias.textContent = "Also: " + e.aliases.join(", ");
    content.appendChild(alias);
  }
  const intro = document.createElement("p");
  intro.className = "entity-intro";
  intro.textContent = `First known at chapter ${e.first_seen}.`;
  content.appendChild(intro);

  const cur = currentSummary(e.id);
  if (cur) {
    const box = document.createElement("div");
    box.className = "summary";
    appendProse(box, cur.text, "summary-text");
    content.appendChild(box);
  }
  for (const prior of priorSummaries(e.id)) {
    const det = document.createElement("details");
    det.className = "prior-summary";
    const sum = document.createElement("summary");
    sum.textContent =
      `Earlier understanding (ch ${prior.established_at}–${prior.revoked_at})`;
    det.appendChild(sum);
    const body = document.createElement("div");
    body.className = "prior-summary-body";
    appendProse(body, prior.text, "summary-text");
    det.appendChild(body);
    content.appendChild(det);
  }

  const fs = activeEntityFacts(e.id).sort(
    (a, b) => a.established_at - b.established_at);
  const h = document.createElement("h3");
  h.className = "facts-heading";
  h.textContent = `Facts known by chapter ${chapter} (${fs.length})`;
  content.appendChild(h);

  for (const f of fs) {
    content.appendChild(factCard(f));
  }

  if (fs.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Nothing specific established yet for this entity.";
    content.appendChild(p);
  }
}

function renderAbout(sidebar, content) {
  sidebar.innerHTML = "";
  content.innerHTML = "";

  const box = document.createElement("div");
  box.className = "about";
  box.innerHTML = [
    "<h2>About this wiki</h2>",
    "<p>A spoiler-safe, chapter-timestamped wiki of <em>Lord of the Mysteries</em>. Move the Chapter slider and the whole wiki rewinds or fast-forwards: you see only what is knowable at that point in the story.</p>",
    "<ul>",
    "  <li><strong>Timestamped knowledge.</strong> Facts and descriptions exist only from the chapter that establishes them \u2014 nothing from later chapters leaks backward.</li>",
    "  <li><strong>Reversals, kept.</strong> When a later chapter overturns an earlier understanding, the old one isn\u2019t erased \u2014 it stays visible as <em>previous understanding</em>, with sources, alongside what replaced it.</li>",
    "  <li><strong>Sourced.</strong> Every fact cites its chapter and an exact quote from the text.</li>",
    "</ul>",
    "<p>Browse <strong>Entities</strong> for people, places, and concepts, or <strong>Temporal changes</strong> to see how the wiki\u2019s understanding has shifted. Content so far covers chapters 1\u2013" + meta.max_chapter + ".</p>",
    "<p class=\"about-note\">Unofficial fan reference, not affiliated with the author or publisher. See the project README for full attribution.</p>",
  ].join("");
  content.appendChild(box);
}

function renderChanges(sidebar, content) {
  sidebar.innerHTML = "";
  content.innerHTML = "";

  const p = document.createElement("p");
  p.className = "changes-intro";
  p.textContent =
    "How the wiki's understanding has shifted, up to this chapter. " +
    "Overturned facts show the previous understanding alongside what replaced it.";
  content.appendChild(p);

  // Refinements: base facts that have been extended (still true).
  const refined = facts.filter(
    (f) => f.refined_by.length > 0 && isActiveAt(f, chapter) &&
      f.refined_by.some((id) => factMap[id].established_at <= chapter));
  if (refined.length) {
    const h = document.createElement("h2");
    h.textContent = "Refinements (understanding deepened)";
    content.appendChild(h);
    for (const f of refined.sort((a, b) => a.established_at - b.established_at)) {
      const card = document.createElement("div");
      card.className = "change refined";
      card.appendChild(factCard(f));
      const kids = document.createElement("div");
      kids.className = "change-detail";
      kids.innerHTML = "<strong>Refined by:</strong>";
      for (const id of f.refined_by) {
        if (factMap[id].established_at <= chapter) {
          kids.appendChild(factCard(factMap[id]));
        }
      }
      card.appendChild(kids);
      content.appendChild(card);
    }
  }

  // Overturns: facts that were superseded by this chapter.
  const overturned = facts.filter(
    (f) => f.superseded_by.length > 0 && f.revoked_at !== null &&
      f.revoked_at <= chapter);
  if (overturned.length) {
    const h = document.createElement("h2");
    h.textContent = "Overturned (previous understandings)";
    content.appendChild(h);
    for (const f of overturned.sort((a, b) => a.revoked_at - b.revoked_at)) {
      const card = document.createElement("div");
      card.className = "change overturned";
      const oldLabel = document.createElement("p");
      oldLabel.className = "change-label";
      oldLabel.textContent =
        `Previously believed (ch ${f.established_at} → ${f.revoked_at}):`;
      card.appendChild(oldLabel);
      card.appendChild(factCard(f));
      for (const id of f.superseded_by) {
        const rep = factCard(factMap[id]);
        rep.classList.add("replacement");
        card.appendChild(rep);
      }
      content.appendChild(card);
    }
  }

  // Summary revisions: descriptions that were replaced by this chapter.
  const revSummary = summaries.filter(
    (s) => s.revoked_at !== null && s.revoked_at <= chapter);
  if (revSummary.length) {
    const h = document.createElement("h2");
    h.textContent = "Summary revisions (descriptions that changed)";
    content.appendChild(h);
    for (const s of revSummary.sort((a, b) => a.revoked_at - b.revoked_at)) {
      const card = document.createElement("div");
      card.className = "change overturned";
      const lbl = document.createElement("p");
      lbl.className = "change-label";
      lbl.textContent =
        `${entityMap[s.entity].name} — description ch ${s.established_at} → ${s.revoked_at}:`;
      card.appendChild(lbl);
      const oldBox = document.createElement("div");
      oldBox.className = "summary";
      appendProse(oldBox, s.text, "summary-text");
      card.appendChild(oldBox);
      for (const id of s.superseded_by) {
        const rep = summaries.find((x) => x.id === id);
        const newBox = document.createElement("div");
        newBox.className = "summary replacement";
        appendProse(newBox, rep.text, "summary-text");
        card.appendChild(newBox);
      }
      content.appendChild(card);
    }
  }

  if (!refined.length && !overturned.length && !revSummary.length) {
    const p2 = document.createElement("p");
    p2.className = "empty hint";
    p2.textContent =
      "No shifts yet up to this chapter — the early book is mostly accretion, " +
      "not reversal.";
    content.appendChild(p2);
  }
}

function factCard(f) {
  const card = document.createElement("div");
  card.className = "fact-card";
  card.style.setProperty("--order", f.established_at);

  const top = document.createElement("div");
  top.className = "fact-top";

  const stmt = document.createElement("p");
  stmt.className = "fact-statement";
  stmt.innerHTML = linkify(f.statement);
  top.appendChild(stmt);

  const cert = document.createElement("span");
  cert.className = "certainty " + f.certainty;
  cert.textContent = certaintyLabel[f.certainty];
  top.appendChild(cert);

  card.appendChild(top);

  const srcs = document.createElement("ul");
  srcs.className = "sources";
  for (const s of f.sources) {
    const li = document.createElement("li");
    const ch = document.createElement("span");
    ch.className = "source-ch";
    ch.textContent = "ch " + s.chapter;
    li.appendChild(ch);
    if (s.quote) {
      const q = document.createElement("span");
      q.className = "source-quote";
      q.textContent = s.quote;
      li.appendChild(q);
    }
    srcs.appendChild(li);
  }
  card.appendChild(srcs);

  return card;
}

init().catch((err) => {
  document.getElementById("content").textContent =
    "Error loading data: " + err.message +
    " — run `uv run python build.py` first.";
});