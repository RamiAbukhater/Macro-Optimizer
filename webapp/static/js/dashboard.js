const raw = sessionStorage.getItem("macroPlan");
if (!raw) {
  window.location.href = "/";
}
const plan = JSON.parse(raw);

const CHANNEL_COLOR = {
  "Cook at Home": "var(--series-1)",
  "Prepared Meal Delivery": "var(--series-3)",
  "Restaurant Order": "var(--series-2)",
};

const CHANNEL_BADGE_CLASS = {
  "Cook at Home": "badge-channel-home",
  "Prepared Meal Delivery": "badge-channel-prepared",
  "Restaurant Order": "badge-channel-restaurant",
};

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function renderStatRow() {
  const row = document.getElementById("stat-row");
  const tiles = [
    { label: "Goal", value: plan.profile.goal_label, sub: plan.profile.activity_label },
    { label: "BMR", value: `${plan.profile.bmr}`, sub: "kcal/day at rest" },
    { label: "Maintenance (TDEE)", value: `${plan.profile.tdee}`, sub: "kcal/day" },
    { label: "Daily calorie target", value: `${plan.goals.calories}`, sub: `${plan.goals.protein}g P · ${plan.goals.carbs}g C · ${plan.goals.fat}g F` },
    { label: "Daily budget", value: `$${plan.budget.toFixed(2)}`, sub: `$${plan.week_summary.avg_daily_cost.toFixed(2)} avg spent` },
  ];
  tiles.forEach((t) => {
    const tile = el("div", "stat-tile");
    tile.appendChild(el("div", "stat-label", t.label));
    tile.appendChild(el("div", "stat-value", t.value));
    tile.appendChild(el("div", "stat-sub", t.sub));
    row.appendChild(tile);
  });
}

function itemMetaLine(item) {
  return `${item.calories.toFixed(0)} kcal · ${item.protein.toFixed(0)}g P · ${item.carbs.toFixed(0)}g C · ${item.fat.toFixed(0)}g F`;
}

function renderToday() {
  document.getElementById("today-date").textContent = formatDate(plan.today.date);
  const list = document.getElementById("today-list");

  if (plan.today.items.length === 0) {
    list.appendChild(el("div", "empty-state", "No feasible plan for today within budget and time. Try raising the budget or cook time."));
    return;
  }

  plan.today.items.forEach((item) => {
    const li = el("li");
    const left = el("div");
    left.appendChild(el("div", "plan-item-name", `${item.name} ${item.servings > 1 ? `×${item.servings}` : ""}`));
    const badgeClass = CHANNEL_BADGE_CLASS[item.channel] || "badge-channel-home";
    left.appendChild(el("div", "plan-item-meta", `${itemMetaLine(item)} <span class="badge ${badgeClass}" style="margin-left:6px;">${item.channel}</span>`));
    li.appendChild(left);
    li.appendChild(el("div", "plan-item-cost", `$${item.total_cost.toFixed(2)}`));
    list.appendChild(li);
  });

  const statusBadge = plan.today.feasible
    ? `<span class="badge badge-good">On target</span>`
    : `<span class="badge badge-warn">Best effort</span>`;
  const summary = el("li");
  summary.style.borderTop = "2px solid var(--gridline)";
  summary.innerHTML = `<div class="plan-item-name">Total ${statusBadge}</div>`;
  summary.appendChild(el("div", "plan-item-cost", `$${plan.today.achieved.cost.toFixed(2)}`));
  list.appendChild(summary);
}

function renderWeek() {
  const grid = document.getElementById("week-grid");
  plan.week.forEach((day) => {
    const card = el("div", "day-card");
    const header = el("div", "day-header");
    header.appendChild(el("div", "day-name", formatDate(day.date)));
    header.appendChild(el("div", "day-cost", `$${day.achieved.cost.toFixed(2)}`));
    card.appendChild(header);

    if (!day.feasible) {
      card.appendChild(el("span", "badge badge-warn", "Best effort"));
    }

    const channelsUsed = [...new Set(day.items.map((i) => i.channel))];
    if (channelsUsed.length) {
      const badges = el("div");
      badges.style.marginTop = "6px";
      channelsUsed.forEach((c) => {
        const b = el("span", `badge ${CHANNEL_BADGE_CLASS[c]}`, c);
        b.style.marginRight = "4px";
        badges.appendChild(b);
      });
      card.appendChild(badges);
    }

    const list = el("ul", "item-list");
    day.items.forEach((item) => {
      const li = el("li");
      li.innerHTML = `<span>${item.name}${item.servings > 1 ? ` ×${item.servings}` : ""}</span><span>$${item.total_cost.toFixed(2)}</span>`;
      list.appendChild(li);
    });
    if (day.items.length === 0) {
      list.appendChild(el("li", "", "No feasible plan"));
    }
    card.appendChild(list);

    grid.appendChild(card);
  });
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

function renderBudget() {
  const weeklyBudget = plan.budget * plan.week.length;
  const pct = Math.min(100, (plan.week_summary.total_cost / weeklyBudget) * 100);
  document.getElementById("budget-meter-value").textContent =
    `$${plan.week_summary.total_cost.toFixed(2)} / $${weeklyBudget.toFixed(2)} (${pct.toFixed(0)}%)`;
  document.getElementById("budget-meter-fill").style.width = `${pct}%`;
}

function renderChannelBars() {
  const container = document.getElementById("channel-bars");
  const entries = Object.entries(plan.channel_totals).sort((a, b) => b[1].cost - a[1].cost);
  if (entries.length === 0) {
    container.appendChild(el("div", "empty-state", "No items were selected this week."));
    return;
  }
  const maxCost = Math.max(...entries.map(([, v]) => v.cost));

  entries.forEach(([channel, v]) => {
    const row = el("div", "meter-row");
    const pct = maxCost > 0 ? (v.cost / maxCost) * 100 : 0;
    const label = el("div", "meter-label");
    label.innerHTML = `<span class="name">${channel}</span><span class="value">$${v.cost.toFixed(2)} · ${v.items} item${v.items === 1 ? "" : "s"}</span>`;
    row.appendChild(label);
    const track = el("div", "meter-track");
    const fill = el("div", "meter-fill");
    fill.style.width = `${pct}%`;
    fill.style.background = CHANNEL_COLOR[channel] || "var(--series-1)";
    track.appendChild(fill);
    row.appendChild(track);
    container.appendChild(row);
  });

  if (plan.week_summary.infeasible_days.length > 0) {
    const note = el("p", "", `${plan.week_summary.infeasible_days.length} day(s) this week were best-effort (couldn't fully meet goals within budget/time): ${plan.week_summary.infeasible_days.join(", ")}.`);
    note.style.marginTop = "12px";
    note.style.fontSize = "13px";
    container.appendChild(note);
  }
}

document.getElementById("goal-heading").textContent = `Your plan to ${plan.profile.goal_label.toLowerCase()}`;
renderStatRow();
renderToday();
renderWeek();
renderMacroMeters();
renderBudget();
renderChannelBars();

document.getElementById("loading").style.display = "none";
document.getElementById("dashboard-root").style.display = "block";
