// Natural-language plan adjustments. Depends on globals from dashboard.js/menu.js:
// `plan`, and whichever of `renderWeek`/`renderToday`/`renderMacroMeters` the
// current page defines (feature-detected below, since no single page defines
// all three post the dashboard/menu/cost/fridge split).
//
// Contract with POST /api/plan/adjust (see NLU_ADJUSTMENTS.md, webapp/app.py):
//   request:  {"message": "<text>"} or {"message": "<text>", "confirmed": true}
//   response: {"status": "needs_clarification", "question": "...", "options": [...]}
//          or {"status": "confirm", "directive": {...}, "preview": "..."}
//          or {"day": {...}} or {"day": {...}, "next_day": {...}} on apply
//          or {"error": "..."} (non-2xx)

const adjustForm = document.getElementById("adjust-form");
const adjustInput = document.getElementById("adjust-input");
const adjustSubmit = document.getElementById("adjust-submit");
const adjustResponse = document.getElementById("adjust-response");

// Renders user/model-supplied text as textContent, never innerHTML, since
// adjustment messages and NLU replies can echo arbitrary user input back.
function textEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clearAdjustResponse() {
  adjustResponse.innerHTML = "";
}

function showAdjustError(message) {
  clearAdjustResponse();
  adjustResponse.appendChild(textEl("p", "adjust-error", message));
}

async function postAdjustment(message, confirmed) {
  const payload = confirmed ? { message, confirmed: true } : { message };
  return postJSON("/api/plan/adjust", payload);
}

// Updates plan.week/plan.today in place for the returned day(s), persists the
// plan, then re-renders by clearing and re-invoking whichever render
// functions the current page actually defines.
function applyDayUpdates(days) {
  const todayDate = plan.today.date;
  let touchesToday = false;

  days.forEach((day) => {
    const idx = plan.week.findIndex((d) => d.date === day.date);
    if (idx !== -1) plan.week[idx] = day;
    if (day.date === todayDate) touchesToday = true;
  });
  if (touchesToday) {
    plan.today = plan.week.find((d) => d.date === todayDate) || plan.today;
  }
  savePlan(plan);

  const weekGrid = document.getElementById("week-grid");
  if (weekGrid && typeof renderWeek === "function") {
    weekGrid.innerHTML = "";
    renderWeek();
  }

  if (touchesToday && typeof renderToday === "function") {
    const todayList = document.getElementById("today-list");
    if (todayList) todayList.innerHTML = "";
    renderToday();
    if (typeof renderMacroMeters === "function") {
      const meters = document.getElementById("macro-meters");
      if (meters) meters.innerHTML = "";
      renderMacroMeters();
    }
  }
}

function renderNeedsClarification(body, originalMessage) {
  clearAdjustResponse();
  adjustResponse.appendChild(textEl("p", "", body.question || "Can you clarify?"));
  const options = document.createElement("div");
  (body.options || []).forEach((option) => {
    const btn = textEl("button", "adjust-option", option);
    btn.type = "button";
    // The backend re-parses the whole message from scratch, so picking an
    // option has to carry the original wording along with it, not replace
    // it, or whatever the parser already resolved (the day, the intent) gets
    // thrown away and it just asks the next question in a loop.
    btn.addEventListener("click", () => runAdjustment(`${originalMessage} ${option}`));
    options.appendChild(btn);
  });
  adjustResponse.appendChild(options);
}

function renderConfirm(body, originalMessage) {
  clearAdjustResponse();
  const box = document.createElement("div");
  box.className = "adjust-preview";
  box.appendChild(textEl("p", "", body.preview || "Apply this change?"));

  const actions = document.createElement("div");
  actions.className = "adjust-actions";

  const confirmBtn = textEl("button", "primary", "Confirm");
  confirmBtn.type = "button";
  confirmBtn.addEventListener("click", async () => {
    confirmBtn.disabled = true;
    try {
      const result = await postAdjustment(originalMessage, true);
      handleAdjustResult(result, originalMessage);
    } catch (err) {
      showAdjustError(err.message);
    }
  });

  const cancelBtn = textEl("button", "secondary", "Cancel");
  cancelBtn.type = "button";
  cancelBtn.addEventListener("click", () => clearAdjustResponse());

  actions.appendChild(confirmBtn);
  actions.appendChild(cancelBtn);
  box.appendChild(actions);
  adjustResponse.appendChild(box);
}

function handleAdjustResult(body, originalMessage) {
  if (body.status === "needs_clarification") {
    renderNeedsClarification(body, originalMessage);
    return;
  }
  if (body.status === "confirm") {
    renderConfirm(body, originalMessage);
    return;
  }
  if (body.day) {
    clearAdjustResponse();
    adjustResponse.appendChild(textEl("p", "adjust-success", "Updated."));
    const days = [body.day];
    if (body.next_day) days.push(body.next_day);
    applyDayUpdates(days);
    adjustInput.value = "";
    return;
  }
  showAdjustError("Didn't get a response AutoEat understood.");
}

async function runAdjustment(message) {
  adjustSubmit.disabled = true;
  adjustInput.disabled = true;
  clearAdjustResponse();
  adjustResponse.appendChild(textEl("p", "adjust-status", "Thinking…"));
  try {
    const body = await postAdjustment(message, false);
    handleAdjustResult(body, message);
  } catch (err) {
    showAdjustError(err.message);
  } finally {
    adjustSubmit.disabled = false;
    adjustInput.disabled = false;
  }
}

// Example chips prefill the box rather than submitting, so the wording can be
// edited to fit before anything is sent.
document.querySelectorAll(".adjust-example").forEach((chip) => {
  chip.addEventListener("click", () => {
    adjustInput.value = chip.textContent;
    adjustInput.focus();
  });
});

adjustForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = adjustInput.value.trim();
  if (!message) return;
  runAdjustment(message);
});
