// Per-day cooking time editor. Schedules are 7 minute values, Monday..Sunday,
// matching Python's date.weekday() order on the server (see app.py
// cook_schedule_from). 0 means "no cooking": only zero-prep items fit.

const COOK_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const COOK_PRESETS = [
  { label: "Skip", minutes: 0 },
  { label: "~20", minutes: 20 },
  { label: "~45", minutes: 45 },
  { label: "~90", minutes: 90 },
];
const COOK_TIME_LEGACY = { low: 20, medium: 45, high: 90 };
const COOK_MAX_MINUTES = 240;

/** The 7-day schedule a plan carries, expanding a legacy cook_time label when needed. */
function cookScheduleOf(plan) {
  if (plan && Array.isArray(plan.cook_schedule) && plan.cook_schedule.length === 7) {
    return plan.cook_schedule.slice();
  }
  const m = COOK_TIME_LEGACY[plan && plan.cook_time] || COOK_TIME_LEGACY.medium;
  return Array(7).fill(m);
}

/** Render an editable 7-day schedule into `container`. Calls onChange(schedule) on every edit. */
function renderCookScheduleEditor(container, initial, onChange) {
  const schedule = initial.slice();
  container.innerHTML = "";
  container.classList.add("cook-schedule");

  COOK_DAY_NAMES.forEach((dayName, idx) => {
    const row = el("div", "cook-row");
    row.dataset.day = String(idx);
    row.appendChild(el("div", "cook-day", dayName));

    const chips = el("div", "cook-chips");
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = String(COOK_MAX_MINUTES);
    input.step = "5";
    input.className = "cook-minutes";
    input.setAttribute("aria-label", `${dayName} cooking minutes`);

    const sync = () => {
      input.value = String(schedule[idx]);
      chips.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("selected", Number(b.dataset.minutes) === schedule[idx]);
        b.setAttribute("aria-pressed", Number(b.dataset.minutes) === schedule[idx] ? "true" : "false");
      });
    };

    COOK_PRESETS.forEach((p) => {
      const b = el("button", "cook-chip", p.label);
      b.type = "button";
      b.dataset.minutes = String(p.minutes);
      b.addEventListener("click", () => {
        schedule[idx] = p.minutes;
        sync();
        onChange(schedule.slice());
      });
      chips.appendChild(b);
    });

    input.addEventListener("change", () => {
      let v = Math.round(Number(input.value));
      if (!Number.isFinite(v)) v = 0;
      schedule[idx] = Math.max(0, Math.min(COOK_MAX_MINUTES, v));
      sync();
      onChange(schedule.slice());
    });

    const custom = el("label", "cook-custom");
    custom.appendChild(input);
    custom.appendChild(el("span", null, "min"));

    row.appendChild(chips);
    row.appendChild(custom);
    container.appendChild(row);
    sync();
  });
}

/** Dashboard card: edit the schedule, then rebuild the week with it. */
function mountCookScheduleCard(plan) {
  const mount = document.getElementById("cook-schedule-editor");
  if (!mount) return;
  const saveBtn = document.getElementById("cook-schedule-save");
  const status = document.getElementById("cook-schedule-status");
  const original = cookScheduleOf(plan);
  let current = original.slice();

  renderCookScheduleEditor(mount, original, (next) => {
    current = next;
    saveBtn.disabled = current.every((m, i) => m === original[i]);
    status.textContent = "";
  });
  saveBtn.disabled = true;

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "Rebuilding your week…";
    status.textContent = "";
    try {
      const body = await postJSON("/api/plan", { ...planRequestBase(plan), cook_schedule: current });
      savePlan({ ...body, profile_input: plan.profile_input });
      window.location.reload();
    } catch (err) {
      status.textContent = err.message;
      saveBtn.disabled = false;
      saveBtn.textContent = "Save & rebuild week";
    }
  });
}
