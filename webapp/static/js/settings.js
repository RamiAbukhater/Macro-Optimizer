/* Settings page: edit the profile_settings blob (GET/PUT /api/settings).
 *
 * Saving profile settings rebuilds the week from today whenever a plan
 * exists (server-side, same call), so the plan on screen always matches the
 * saved settings; one level of undo (/api/plan/undo) restores both. With no
 * plan yet, saving only stores the settings for onboarding to pick up.
 */

const FILTER_NAMES = ["vegetarian", "vegan", "halal", "gluten_free", "no_pork"];
const NUMBER_FIELDS = ["age", "weight_lbs", "height_ft", "height_in", "weekly_budget"];
const TARGET_LABELS = [
  { key: "calories", label: "Calories", unit: "kcal" },
  { key: "protein", label: "Protein", unit: "g" },
  { key: "carbs", label: "Carbs", unit: "g" },
  { key: "fat", label: "Fat", unit: "g" },
];

const state = {
  saved: null,      // settings as stored on the server
  draft: null,      // settings as edited on the page
  savedGoals: null, // macro targets for `saved`
  hasPlan: false,
  canUndo: false,
};

const $ = (id) => document.getElementById(id);

function clone(o) {
  return JSON.parse(JSON.stringify(o));
}

function isDirty() {
  return state.saved && JSON.stringify(normalize(state.draft)) !== JSON.stringify(normalize(state.saved));
}

/** Comparable/sendable form: numbers as numbers, every filter present. */
function normalize(s) {
  const out = { ...s, filters: {} };
  NUMBER_FIELDS.forEach((k) => { out[k] = Number(s[k]); });
  FILTER_NAMES.forEach((k) => { out.filters[k] = Boolean(s.filters && s.filters[k]); });
  out.cook_schedule = (s.cook_schedule || []).map(Number);
  return out;
}

/* ---------- Body & goals / food / planning form ---------- */

function fillForm(s) {
  ["sex", "activity", ...NUMBER_FIELDS].forEach((k) => {
    const input = $(`s-${k}`);
    if (input && s[k] != null) input.value = s[k];
  });
  document.querySelectorAll("#s-goal-cards .goal-card").forEach((card) => {
    const on = card.dataset.value === s.goal;
    card.classList.toggle("selected", on);
    card.setAttribute("aria-checked", on ? "true" : "false");
  });
  FILTER_NAMES.forEach((k) => { $(`sf-${k}`).checked = Boolean(s.filters && s.filters[k]); });
  renderCookScheduleEditor($("s-cook-schedule"), cookScheduleOf(s), (next) => {
    state.draft.cook_schedule = next;
    onDraftChange();
  });
  updateBudgetHint();
}

function readFieldsIntoDraft() {
  ["sex", "activity"].forEach((k) => { state.draft[k] = $(`s-${k}`).value; });
  NUMBER_FIELDS.forEach((k) => { state.draft[k] = $(`s-${k}`).value === "" ? null : Number($(`s-${k}`).value); });
  state.draft.filters = {};
  FILTER_NAMES.forEach((k) => { state.draft.filters[k] = $(`sf-${k}`).checked; });
}

function updateBudgetHint() {
  const weekly = Number($("s-weekly_budget").value);
  $("s-weekly_budget_hint").textContent = weekly > 0
    ? `${formatMoney(weekly / PLAN_DAYS)}/day · ${monthlyHint(weekly)}`
    : "";
}

function renderTargets(goals, previous) {
  const row = $("targets-row");
  row.innerHTML = "";
  if (!goals) {
    row.appendChild(el("div", "hint", "Fill in every field to see your targets."));
    return;
  }
  TARGET_LABELS.forEach(({ key, label, unit }) => {
    const tile = el("div", "target-tile");
    tile.appendChild(el("div", "stat-label", label));
    tile.appendChild(el("div", "target-value", `${goals[key]}<span>${unit}</span>`));
    const diff = previous ? goals[key] - previous[key] : 0;
    if (diff !== 0) {
      tile.appendChild(el("div", `target-delta ${diff > 0 ? "up" : "down"}`, `${diff > 0 ? "+" : "−"}${Math.abs(diff)} vs now`));
    }
    row.appendChild(tile);
  });
}

let previewTimer = null;
let previewSeq = 0;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const seq = ++previewSeq;
    try {
      const body = await postJSON("/api/settings/preview", { settings: normalize(state.draft) });
      if (seq === previewSeq) renderTargets(body.goals, state.savedGoals);
    } catch (err) {
      if (seq === previewSeq) renderTargets(null);
    }
  }, 250);
}

function updateSaveBar(message) {
  const dirty = isDirty();
  const saveBtn = $("save-btn");
  saveBtn.disabled = !dirty;
  saveBtn.textContent = state.hasPlan ? "Save & rebuild week" : "Save settings";
  $("discard-btn").hidden = !dirty;
  $("undo-btn").hidden = dirty || !state.canUndo;
  $("save-bar").classList.toggle("is-dirty", dirty);

  if (message) {
    $("save-bar-text").textContent = message;
  } else if (dirty) {
    $("save-bar-text").textContent = state.hasPlan
      ? "Unsaved changes. Saving rebuilds your 7-day plan starting today."
      : "Unsaved changes. You don't have a plan yet, so this just saves them.";
  } else if (!state.canUndo) {
    $("save-bar-text").textContent = "No unsaved changes";
  }
}

function onDraftChange() {
  $("settings-error").style.display = "none";
  state.canUndo = false;
  updateSaveBar();
  schedulePreview();
}

function showError(message) {
  const banner = $("settings-error");
  banner.textContent = message;
  banner.style.display = "block";
}

function setBusy(text) {
  $("loading-text").textContent = text;
  $("loading").style.display = text ? "flex" : "none";
}

async function saveSettings() {
  readFieldsIntoDraft();
  const rebuilding = state.hasPlan;
  setBusy(rebuilding ? "Rebuilding your week…" : "Saving…");
  try {
    const body = await apiJSON("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ settings: normalize(state.draft) }),
    });
    state.saved = clone(body.settings);
    state.draft = clone(body.settings);
    state.savedGoals = body.goals;
    if (body.plan) savePlan(body.plan); // other pages read the plan from sessionStorage
    state.canUndo = Boolean(body.plan);
    renderTargets(state.savedGoals, null);
    updateSaveBar(rebuilding
      ? "Saved. Your week was rebuilt from today."
      : "Saved. Your next plan will use these.");
    toast(rebuilding ? "Settings saved, week rebuilt" : "Settings saved");
  } catch (err) {
    showError(err.message);
  } finally {
    setBusy(null);
  }
}

async function undoRebuild() {
  setBusy("Restoring your previous week…");
  try {
    const previous = await postJSON("/api/plan/undo", {});
    savePlan(previous);
    await load();
    state.canUndo = false;
    updateSaveBar("Restored your previous week and settings.");
    toast("Undone");
  } catch (err) {
    showError(err.message);
  } finally {
    setBusy(null);
  }
}

/* ---------- Taste profile ---------- */

function renderTaste(taste) {
  [["liked", taste.liked], ["disliked", taste.disliked]].forEach(([kind, names]) => {
    const list = $(`${kind}-list`);
    $(`${kind}-count`).textContent = names.length;
    list.innerHTML = "";
    if (names.length === 0) {
      list.appendChild(el("span", "hint", kind === "liked" ? "Nothing yet" : "Nothing yet"));
      return;
    }
    names.forEach((name) => {
      const chip = el("span", `taste-chip taste-${kind}`);
      chip.appendChild(el("span", null, name));
      const remove = el("button", "taste-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Forget ${name}`);
      remove.addEventListener("click", async () => {
        try {
          const body = await postJSON("/api/settings/taste", { remove: [name] });
          renderTaste(body.taste);
        } catch (err) {
          toast(err.message);
        }
      });
      chip.appendChild(remove);
      list.appendChild(chip);
    });
  });
  $("clear-taste-btn").disabled = taste.liked.length + taste.disliked.length === 0;
}

let surveyLoaded = false;
function openSurvey() {
  $("survey-modal").classList.add("show");
  if (surveyLoaded) return;
  surveyLoaded = true;
  const script = document.createElement("script");
  script.src = "/js/swipe-survey.js";
  document.body.appendChild(script);
}

async function closeSurvey() {
  if (window.swipeSurvey) await window.swipeSurvey.finalize();
  $("survey-modal").classList.remove("show");
  const body = await getJSON("/api/settings");
  renderTaste(body.taste);
  // A fresh stack next time: swipe-survey.js only runs one pass per load.
  surveyLoaded = false;
  document.querySelectorAll('script[src="/js/swipe-survey.js"]').forEach((s) => s.remove());
  resetSurveyMarkup();
}

const SURVEY_MARKUP = $("swipe-card").innerHTML;
function resetSurveyMarkup() {
  const card = $("swipe-card");
  card.innerHTML = SURVEY_MARKUP;
  card.style.display = "";
}

/* ---------- Account ---------- */

async function changePassword(e) {
  e.preventDefault();
  const status = $("pw-status");
  status.className = "form-status";
  status.textContent = "";
  $("pw-submit").disabled = true;
  try {
    await postJSON("/api/account/password", {
      current_password: $("pw-current").value,
      new_password: $("pw-new").value,
    });
    $("password-form").reset();
    status.classList.add("ok");
    status.textContent = "Password updated.";
  } catch (err) {
    status.classList.add("bad");
    status.textContent = err.message;
  } finally {
    $("pw-submit").disabled = false;
  }
}

function syncDeleteButton() {
  $("delete-submit").disabled = !($("del-confirm").value === "DELETE" && $("del-password").value);
}

async function deleteAccount(e) {
  e.preventDefault();
  const status = $("del-status");
  status.className = "form-status";
  status.textContent = "";
  $("delete-submit").disabled = true;
  try {
    await apiJSON("/api/account", {
      method: "DELETE",
      body: JSON.stringify({ password: $("del-password").value, confirm: $("del-confirm").value }),
    });
    sessionStorage.clear();
    window.location.href = "/signup";
  } catch (err) {
    status.classList.add("bad");
    status.textContent = err.message;
    syncDeleteButton();
  }
}

/* ---------- Load & wire up ---------- */

async function load() {
  const body = await getJSON("/api/settings");
  state.hasPlan = body.has_plan;
  $("acct-email").textContent = body.account.email;
  $("acct-username").textContent = body.account.username;
  $("pw-username").value = body.account.username;
  renderTaste(body.taste);

  if (!body.settings) {
    $("no-settings").hidden = false;
    $("settings-form").hidden = true;
    document.querySelectorAll('.settings-jump a[href="#body-section"], .settings-jump a[href="#food-section"], .settings-jump a[href="#planning-section"]')
      .forEach((a) => { a.hidden = true; });
    return;
  }
  state.saved = clone(body.settings);
  state.draft = clone(body.settings);
  state.savedGoals = body.goals;
  fillForm(state.draft);
  renderTargets(state.savedGoals, null);
  updateSaveBar();
}

function wire() {
  const form = $("settings-form");
  form.addEventListener("input", (e) => {
    if (!e.target.matches("input, select") || e.target.closest("#s-cook-schedule")) return;
    readFieldsIntoDraft();
    if (e.target.id === "s-weekly_budget") updateBudgetHint();
    onDraftChange();
  });
  form.addEventListener("change", (e) => {
    if (e.target.matches("select, input[type=checkbox]")) {
      readFieldsIntoDraft();
      onDraftChange();
    }
  });
  document.querySelectorAll("#s-goal-cards .goal-card").forEach((card) => {
    card.addEventListener("click", () => {
      state.draft.goal = card.dataset.value;
      document.querySelectorAll("#s-goal-cards .goal-card").forEach((c) => {
        c.classList.toggle("selected", c === card);
        c.setAttribute("aria-checked", c === card ? "true" : "false");
      });
      onDraftChange();
    });
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (isDirty()) saveSettings();
  });
  $("discard-btn").addEventListener("click", () => {
    state.draft = clone(state.saved);
    fillForm(state.draft);
    renderTargets(state.savedGoals, null);
    $("settings-error").style.display = "none";
    updateSaveBar();
  });
  $("undo-btn").addEventListener("click", undoRebuild);

  $("retake-btn").addEventListener("click", openSurvey);
  $("survey-done-btn").addEventListener("click", closeSurvey);
  $("clear-taste-btn").addEventListener("click", async () => {
    if (!window.confirm("Forget every like and dislike from your swipes?")) return;
    const body = await postJSON("/api/settings/taste", { clear: true });
    renderTaste(body.taste);
  });

  $("password-form").addEventListener("submit", changePassword);
  $("logout-btn").addEventListener("click", async () => {
    try { await postJSON("/api/auth/logout", {}); } catch (err) { /* redirect anyway */ }
    sessionStorage.clear();
    window.location.href = "/login";
  });

  const modal = $("delete-modal");
  $("delete-open-btn").addEventListener("click", () => {
    $("delete-form").reset();
    $("del-status").textContent = "";
    syncDeleteButton();
    modal.classList.add("show");
    $("del-password").focus();
  });
  $("delete-cancel").addEventListener("click", () => modal.classList.remove("show"));
  $("del-confirm").addEventListener("input", syncDeleteButton);
  $("del-password").addEventListener("input", syncDeleteButton);
  $("delete-form").addEventListener("submit", deleteAccount);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") modal.classList.remove("show");
  });

  window.addEventListener("beforeunload", (e) => {
    if (isDirty()) e.preventDefault();
  });
}

(async function init() {
  renderShell("settings");
  wire();
  try {
    await load();
  } catch (err) {
    showError(`Couldn't load your settings: ${err.message}`);
  }
  setBusy(null);
  $("settings-root").style.display = "block";
})();
