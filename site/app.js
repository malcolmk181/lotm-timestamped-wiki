"use strict";

// ---- state ---------------------------------------------------------------
let facts = [];
let entities = [];
let meta = { max_chapter: 1 };
let chapter = 1;
let view = "entities";
let selectedId = null;

const byId = (arr) => Object.fromEntries(arr.map((e) => [e.id, e]));
let entityMap = {};
let factMap = {};

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

// ---- loading -------------------------------------------------------------
async function load(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`failed to load ${path}: ${res.status}`);
  return res.json();
}

async function init() {
  [facts, entities, meta] = await Promise.all([
    load("data/facts.json"),
    load("data/entities.json"),
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

  render();
}

// ---- rendering -----------------------------------------------------------
function render() {
  updateTopbar();
  const sidebar = document.getElementById("sidebar");
  const content = document.getElementById("content");
  const footer = document.getElementById("footer");
  footer.textContent =
    `${meta.entity_count} entities · ${meta.fact_count} facts · ` +
    `showing world as of chapter ${chapter}`;

  if (view === "entities") {
    renderEntityIndex(sidebar);
    renderEntityDetail(content);
  } else {
    renderChanges(sidebar, content);
  }
}

function updateTopbar() {
  document.getElementById("chapter-value").textContent =
    `${chapter} / ${meta.max_chapter}`;
}

function activeEntityFacts(eid) {
  return facts.filter(
    (f) => f.entities.includes(eid) && isActiveAt(f, chapter));
}

function entityCount(eid) {
  return activeEntityFacts(eid).length;
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
  if (e.summary) {
    const sum = document.createElement("p");
    sum.className = "entity-summary";
    sum.textContent = e.summary;
    content.appendChild(sum);
  }
  const intro = document.createElement("p");
  intro.className = "entity-intro";
  intro.textContent = `First known at chapter ${e.first_seen}.`;
  content.appendChild(intro);

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

  if (!refined.length && !overturned.length) {
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
  stmt.textContent = f.statement;
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