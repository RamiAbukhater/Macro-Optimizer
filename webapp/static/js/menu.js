let plan = loadPlan();

function itemMetaLine(item) {
  return `${item.calories.toFixed(0)} kcal · ${item.protein.toFixed(0)}g P · ${item.carbs.toFixed(0)}g C · ${item.fat.toFixed(0)}g F`;
}

function dayCurrentItems(day) {
  return day.items.map((i) => ({ name: i.name, source: i.source, servings: i.servings }));
}

async function handleReplace(day, item, btn) {
  btn.classList.add("loading");
  try {
    const body = {
      ...planRequestBase(plan),
      date: day.date,
      days_out: day.days_out,
      current_items: dayCurrentItems(day),
      replace_name: item.name,
    };
    const resp = await postJSON("/api/plan/replace-item", body);
    const dayIndex = plan.week.findIndex((d) => d.date === day.date);
    plan.week[dayIndex] = resp.day;
    recomputeAggregates(plan);
    savePlan(plan);
    renderWeek();
    toast(`Swapped out ${item.name}`);
  } catch (err) {
    toast(err.message || "Couldn't find a replacement for that item.");
    btn.classList.remove("loading");
  }
}

async function handleReshuffle() {
  const btn = document.getElementById("reshuffle-btn");
  btn.disabled = true;
  btn.querySelector("#reshuffle-icon").classList.add("icon-spin");
  try {
    const weekItems = plan.week.flatMap((day) =>
      day.items.map((item) => ({ date: day.date, name: item.name, servings: item.servings }))
    );
    const body = { ...planRequestBase(plan), week_items: weekItems };
    const resp = await postJSON("/api/plan/reshuffle", body);
    plan = { ...plan, ...resp };
    savePlan(plan);
    renderWeek();
    toast("Week reshuffled");
  } catch (err) {
    toast(err.message || "Couldn't reshuffle the week.");
  } finally {
    btn.disabled = false;
    const icon = document.getElementById("reshuffle-icon");
    if (icon) icon.classList.remove("icon-spin");
  }
}

function renderWeek() {
  const grid = document.getElementById("week-grid");
  grid.innerHTML = "";

  plan.week.forEach((day) => {
    const card = el("div", "day-card");
    const header = el("div", "day-header");
    header.appendChild(el("div", "day-name", formatDate(day.date)));
    header.appendChild(el("div", "day-cost", formatMoney(day.achieved.cost)));
    card.appendChild(header);

    const channelsUsed = [...new Set(day.items.map((i) => i.channel))];
    const badgesRow = el("div", "badges-row");
    if (!day.feasible) badgesRow.appendChild(el("span", "badge badge-warn", "Best effort"));
    channelsUsed.forEach((c) => badgesRow.appendChild(el("span", `badge ${CHANNEL_BADGE_CLASS[c]}`, c)));
    if (badgesRow.children.length) card.appendChild(badgesRow);

    const list = el("ul", "item-list");
    day.items.forEach((item) => {
      const li = el("li", "day-item-row");
      const left = el("div");
      left.style.minWidth = "0";
      left.innerHTML = `<a class="item-link" href="${recipeHref(item)}">${item.name}</a>${item.servings > 1 ? ` ×${item.servings}` : ""}
        <div class="plan-item-meta" style="font-size:11px;">${itemMetaLine(item)}</div>`;
      li.appendChild(left);

      const right = el("div");
      right.style.display = "flex";
      right.style.alignItems = "center";
      right.style.gap = "4px";
      right.appendChild(el("span", "item-cost", formatMoney(item.total_cost)));
      const replaceBtn = el("button", "item-replace-btn", ICONS.swap);
      replaceBtn.title = `Replace ${item.name}`;
      replaceBtn.addEventListener("click", () => handleReplace(day, item, replaceBtn));
      right.appendChild(replaceBtn);
      li.appendChild(right);

      list.appendChild(li);
    });
    if (day.items.length === 0) {
      list.appendChild(el("li", "", `<div class="empty-state" style="padding:12px 0;">No feasible plan</div>`));
    }
    card.appendChild(list);

    grid.appendChild(card);
  });
}

if (plan) {
  renderShell("menu");
  document.getElementById("reshuffle-icon").innerHTML = ICONS.shuffle;
  document.getElementById("reshuffle-btn").addEventListener("click", handleReshuffle);
  renderWeek();

  document.getElementById("loading").style.display = "none";
  document.getElementById("menu-root").style.display = "block";
}
