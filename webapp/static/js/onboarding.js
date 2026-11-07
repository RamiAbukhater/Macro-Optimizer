const goalCards = document.querySelectorAll(".goal-card");
let selectedGoal = "maintain";

goalCards.forEach((card) => {
  card.addEventListener("click", () => {
    goalCards.forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    selectedGoal = card.dataset.value;
  });
});

const weeklyBudgetInput = document.getElementById("weekly_budget");
const weeklyBudgetHint = document.getElementById("weekly_budget_hint");
function updateWeeklyBudgetHint() {
  const weekly = Number(weeklyBudgetInput.value);
  weeklyBudgetHint.textContent = weekly > 0 ? monthlyHint(weekly) : "";
}
weeklyBudgetInput.addEventListener("input", updateWeeklyBudgetHint);
updateWeeklyBudgetHint();

const COOK_MINUTES = { low: 20, medium: 45, high: 90 };
const weekendToggle = document.getElementById("cook_weekend_different");
const weekendField = document.getElementById("cook_time_weekend_field");
const cookTimeLabel = document.querySelector('label[for="cook_time"]');
weekendToggle.addEventListener("change", () => {
  weekendField.hidden = !weekendToggle.checked;
  cookTimeLabel.textContent = weekendToggle.checked ? "Time to cook on weekdays" : "Time to cook per day";
});

/** Mon..Sun minutes: the main answer for every day, or weekdays + a separate weekend answer. */
function cookScheduleFromForm() {
  const weekday = COOK_MINUTES[document.getElementById("cook_time").value];
  const weekend = weekendToggle.checked
    ? COOK_MINUTES[document.getElementById("cook_time_weekend").value]
    : weekday;
  return [weekday, weekday, weekday, weekday, weekday, weekend, weekend];
}

const form = document.getElementById("profile-form");
const errorBanner = document.getElementById("error-banner");
const submitBtn = document.getElementById("submit-btn");

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.style.display = "block";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBanner.style.display = "none";
  submitBtn.disabled = true;
  submitBtn.textContent = "Building your plan…";

  let preferenceBonus = null;
  if (window.swipeSurvey) {
    preferenceBonus = await window.swipeSurvey.finalize();
  }

  const profileInput = {
    sex: document.getElementById("sex").value,
    age: Number(document.getElementById("age").value),
    height_ft: Number(document.getElementById("height_ft").value),
    height_in: Number(document.getElementById("height_in").value),
    weight_lbs: Number(document.getElementById("weight_lbs").value),
    activity: document.getElementById("activity").value,
    goal: selectedGoal,
  };

  const payload = {
    ...profileInput,
    weekly_budget: Number(document.getElementById("weekly_budget").value),
    cook_time: document.getElementById("cook_time").value,
    cook_schedule: cookScheduleFromForm(),
    filters: {
      vegetarian: document.getElementById("f-vegetarian").checked,
      vegan: document.getElementById("f-vegan").checked,
      halal: document.getElementById("f-halal").checked,
      gluten_free: document.getElementById("f-gluten_free").checked,
      no_pork: document.getElementById("f-no_pork").checked,
    },
  };

  if (preferenceBonus) {
    payload.preference_bonus = preferenceBonus;
  }

  try {
    const resp = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await resp.json();

    if (!resp.ok) {
      showError(body.error || "Something went wrong building your plan.");
      submitBtn.disabled = false;
      submitBtn.textContent = "Build my plan";
      return;
    }

    // Stash the raw form inputs alongside the plan so later pages (menu
    // replace/reshuffle) can rebuild a full plan request without asking the
    // user to re-enter their profile.
    sessionStorage.setItem("macroPlan", JSON.stringify({ ...body, profile_input: profileInput }));
    window.location.href = "/dashboard";
  } catch (err) {
    showError("Could not reach the server. Is the Flask app running?");
    submitBtn.disabled = false;
    submitBtn.textContent = "Build my plan";
  }
});

// Prefill from saved profile settings (e.g. after "Start over"), so redoing
// setup starts from what the user already told us rather than defaults.
getJSON("/api/settings")
  .then(({ settings }) => {
    if (!settings) return;
    ["sex", "age", "height_ft", "height_in", "weight_lbs", "activity", "weekly_budget"].forEach((k) => {
      const input = document.getElementById(k);
      if (input && settings[k] != null) input.value = settings[k];
    });
    goalCards.forEach((c) => c.classList.toggle("selected", c.dataset.value === settings.goal));
    if (settings.goal) selectedGoal = settings.goal;
    Object.entries(settings.filters || {}).forEach(([k, on]) => {
      const box = document.getElementById(`f-${k}`);
      if (box) box.checked = Boolean(on);
    });
    const labelFor = (m) => Object.keys(COOK_MINUTES).find((k) => COOK_MINUTES[k] === m);
    const s = settings.cook_schedule || [];
    if (labelFor(s[0])) document.getElementById("cook_time").value = labelFor(s[0]);
    if (s.length === 7 && s[5] !== s[0] && labelFor(s[5])) {
      weekendToggle.checked = true;
      weekendToggle.dispatchEvent(new Event("change"));
      document.getElementById("cook_time_weekend").value = labelFor(s[5]);
    }
    updateWeeklyBudgetHint();
  })
  .catch(() => { /* no settings yet: keep the defaults */ });
