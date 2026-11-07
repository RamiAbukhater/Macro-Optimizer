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

let weekHasRendered = false;

function renderWeek() {
  const grid = document.getElementById("week-grid");
  grid.innerHTML = "";
  // Tickets drop onto the rail once; swaps and reshuffles re-render in place.
  grid.classList.toggle("is-entering", !weekHasRendered);
  weekHasRendered = true;

  plan.week.forEach((day) => {
    const isToday = day.date === plan.today.date;
    const card = el("article", `day-card ticket${isToday ? " is-today" : ""}`);
    const header = el("div", "day-header");
    const when = dateParts(day.date);
    header.appendChild(el("div", "", `<div class="day-name">${when.weekday}</div><div class="day-date">${when.monthDay}</div>`));
    header.appendChild(el("div", "day-cost", formatMoney(day.achieved.cost)));
    card.appendChild(header);

    const badgesRow = el("div", "badges-row");
    if (isToday) badgesRow.appendChild(el("span", "stamp stamp-hot", "Today"));
    if (!day.feasible) badgesRow.appendChild(el("span", "stamp stamp-warn", "Best effort"));
    if (badgesRow.children.length) card.appendChild(badgesRow);

    const list = el("ul", "item-list");
    day.items.forEach((item) => {
      const li = el("li", "day-item-row");
      const dot = el("span", "chan-dot");
      dot.style.background = CHANNEL_COLOR[item.channel] || "var(--series-1)";
      dot.title = item.channel;
      li.appendChild(dot);

      const left = el("div", "item-main");
      left.innerHTML = `<a class="item-link" href="${recipeHref(item)}">${item.name}</a>${item.servings > 1 ? ` <span class="item-qty">×${item.servings}</span>` : ""}`;
      li.appendChild(left);

      const right = el("div", "item-side");
      right.appendChild(el("span", "item-cost", formatMoney(item.total_cost)));
      const replaceBtn = el("button", "item-replace-btn", ICONS.swap);
      replaceBtn.title = `Replace ${item.name}`;
      replaceBtn.addEventListener("click", () => handleReplace(day, item, replaceBtn));
      right.appendChild(replaceBtn);
      li.appendChild(right);
      li.appendChild(el("div", "plan-item-meta", itemMetaLine(item)));

      list.appendChild(li);
    });
    if (day.items.length === 0) {
      list.appendChild(el("li", "", `<div class="empty-state" style="padding:18px 0 4px;">No feasible plan</div>`));
    }
    card.appendChild(list);

    if (day.items.length) {
      const a = day.achieved;
      card.appendChild(el("div", "day-foot", `<span>${a.calories.toFixed(0)} kcal</span><span>${a.protein.toFixed(0)}P · ${a.carbs.toFixed(0)}C · ${a.fat.toFixed(0)}F</span>`));
    }

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
