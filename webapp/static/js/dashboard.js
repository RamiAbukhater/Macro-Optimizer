const plan = loadPlan();

function itemMetaLine(item) {
  return `${item.calories.toFixed(0)} kcal · ${item.protein.toFixed(0)}g P · ${item.carbs.toFixed(0)}g C · ${item.fat.toFixed(0)}g F`;
}

function renderStatRow() {
  const row = document.getElementById("stat-row");
  const tiles = [
    { label: "Goal", value: plan.profile.goal_label, sub: plan.profile.activity_label },
    { label: "BMR", value: `${plan.profile.bmr}`, sub: "kcal/day at rest" },
    { label: "Maintenance (TDEE)", value: `${plan.profile.tdee}`, sub: "kcal/day" },
    { label: "Daily calorie target", value: `${plan.goals.calories}`, sub: `${plan.goals.protein}g P · ${plan.goals.carbs}g C · ${plan.goals.fat}g F` },
    { label: "Daily budget", value: formatMoney(plan.budget), sub: `${formatMoney(plan.week_summary.avg_daily_cost)} avg spent` },
  ];
  tiles.forEach((t) => {
    const tile = el("div", "stat-tile");
    tile.appendChild(el("div", "stat-label", t.label));
    tile.appendChild(el("div", "stat-value", t.value));
    tile.appendChild(el("div", "stat-sub", t.sub));
    row.appendChild(tile);
  });
}

function renderToday() {
  document.getElementById("today-date").textContent = formatDate(plan.today.date);
  const list = document.getElementById("today-list");

  if (plan.today.items.length === 0) {
    list.appendChild(el("div", "empty-state", `${ICONS.empty}<div>No feasible plan for today within budget and time. Try raising the budget or cook time.</div>`));
    return;
  }

  plan.today.items.forEach((item) => {
    const li = el("li");
    const left = el("div");
    left.appendChild(el("div", "plan-item-name", `<a class="item-link" href="${recipeHref(item)}">${item.name}</a> ${item.servings > 1 ? `×${item.servings}` : ""}`));
    const badgeClass = CHANNEL_BADGE_CLASS[item.channel] || "badge-channel-home";
    left.appendChild(el("div", "plan-item-meta", `${itemMetaLine(item)} <span class="badge ${badgeClass}" style="margin-left:6px;">${item.channel}</span>`));
    li.appendChild(left);
    li.appendChild(el("div", "plan-item-cost", formatMoney(item.total_cost)));
    list.appendChild(li);
  });

  const statusBadge = plan.today.feasible
    ? `<span class="badge badge-good">On target</span>`
    : `<span class="badge badge-warn">Best effort</span>`;
  const summary = el("li");
  summary.style.borderTop = "2px solid var(--gridline)";
  summary.innerHTML = `<div class="plan-item-name">Total ${statusBadge}</div>`;
  summary.appendChild(el("div", "plan-item-cost", formatMoney(plan.today.achieved.cost)));
  list.appendChild(summary);
}

function renderMeter(container, name, achieved, goal, color, track, unit) {
  const row = el("div", "meter-row");
  const pct = goal > 0 ? Math.min(100, (achieved / goal) * 100) : 0;
  const label = el("div", "meter-label");
  label.innerHTML = `<span class="name">${name}</span><span class="value">${achieved.toFixed(0)}${unit} / ${goal.toFixed(0)}${unit}</span>`;
  row.appendChild(label);
  const track_el = el("div", "meter-track");
  track_el.style.background = track;
  const fill = el("div", "meter-fill");
  fill.style.width = `${pct}%`;
  fill.style.background = color;
  track_el.appendChild(fill);
  row.appendChild(track_el);
  container.appendChild(row);
}

function renderMacroMeters() {
  const container = document.getElementById("macro-meters");
  const a = plan.today.achieved;
  const g = plan.goals;
  renderMeter(container, "Calories", a.calories, g.calories, "var(--series-1)", "var(--series-1-track)", "");
  renderMeter(container, "Protein", a.protein, g.protein, "var(--series-2)", "var(--series-2-track)", "g");
  renderMeter(container, "Carbs", a.carbs, g.carbs, "var(--series-3)", "var(--series-3-track)", "g");
  renderMeter(container, "Fat", a.fat, g.fat, "var(--series-4)", "var(--series-4-track)", "g");
}

if (plan) {
  renderShell("overview");
  document.getElementById("goal-heading").textContent = `Your plan to ${plan.profile.goal_label.toLowerCase()}`;
  renderStatRow();
  renderToday();
  renderMacroMeters();
  renderBudgetMeter(plan);
  renderChannelBars(plan);

  document.getElementById("loading").style.display = "none";
  document.getElementById("dashboard-root").style.display = "block";
}
