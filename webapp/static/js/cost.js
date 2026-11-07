const plan = loadPlan();
let shoppingList = null;

function renderStatRow() {
  const row = document.getElementById("stat-row");
  const weeklyBudget = plan.weekly_budget;
  const pct = weeklyBudget > 0 ? (plan.week_summary.total_cost / weeklyBudget) * 100 : 0;
  const tiles = [
    { label: "Weekly total cost", value: formatMoney(plan.week_summary.total_cost), sub: `vs ${formatMoney(weeklyBudget)}/week budget` },
    { label: "Avg daily cost", value: formatMoney(plan.week_summary.avg_daily_cost), sub: `${formatMoney(weeklyBudget / PLAN_DAYS)}/day to stay on budget` },
    { label: "Budget utilization", value: `${pct.toFixed(0)}%`, sub: pct <= 100 ? "within budget" : "over budget" },
    { label: "Shopping list total", value: shoppingList ? formatMoney(shoppingList.total_estimated_cost) : "—", sub: "after pantry offset" },
  ];
  row.innerHTML = "";
  tiles.forEach((t) => {
    const tile = el("div", "stat-tile");
    tile.appendChild(el("div", "stat-label", t.label));
    tile.appendChild(el("div", "stat-value", t.value));
    tile.appendChild(el("div", "stat-sub", t.sub));
    row.appendChild(tile);
  });
}

function flattenWeekItems() {
  return plan.week.flatMap((day) =>
    day.items.map((item) => ({ name: item.name, source: item.source, servings: item.servings, price: item.price, serving_unit: item.serving_unit }))
  );
}

function renderShoppingList() {
  const body = document.getElementById("shopping-list-body");
  body.innerHTML = "";

  if (shoppingList.ingredients.length === 0 && shoppingList.packaged_items.length === 0) {
    body.appendChild(el("div", "empty-state", `${ICONS.empty}<div>Nothing to shop for this week &mdash; every meal is delivered, ordered, or already in your fridge.</div>`));
    return;
  }

  if (shoppingList.ingredients.length) {
    body.appendChild(el("h3", "", "Recipe ingredients"));
    const wrap = el("div", "table-wrap");
    const table = el("table", "data-table");
    table.innerHTML = `<thead><tr><th>Ingredient</th><th class="num">Needed</th><th class="num">Have</th><th class="num">To buy</th><th class="num">Est. price</th></tr></thead>`;
    const tbody = el("tbody");
    shoppingList.ingredients.forEach((i) => {
      const tr = el("tr");
      tr.innerHTML = `
        <td style="text-transform:capitalize;">${i.display_name}</td>
        <td class="num">${i.needed_quantity} ${i.unit}</td>
        <td class="num">${i.have_quantity} ${i.unit}</td>
        <td class="num">${i.to_buy_quantity} ${i.unit}</td>
        <td class="num">${formatMoney(i.estimated_price)}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  if (shoppingList.packaged_items.length) {
    body.appendChild(el("h3", "", shoppingList.ingredients.length ? "Packaged market items" : "Market items"));
    document.querySelector("#shopping-list-body h3:last-of-type").style.marginTop = "20px";
    const wrap = el("div", "table-wrap");
    const table = el("table", "data-table");
    table.innerHTML = `<thead><tr><th>Item</th><th class="num">Quantity</th><th class="num">Est. price</th></tr></thead>`;
    const tbody = el("tbody");
    shoppingList.packaged_items.forEach((i) => {
      const tr = el("tr");
      tr.innerHTML = `<td>${i.name}</td><td class="num">${i.quantity} × ${i.unit}</td><td class="num">${formatMoney(i.estimated_price)}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  if (shoppingList.no_shopping_needed.length) {
    const note = el("p", "", "");
    note.style.marginTop = "18px";
    note.style.fontSize = "12.5px";
    const counts = {};
    shoppingList.no_shopping_needed.forEach((i) => { counts[i.source] = (counts[i.source] || 0) + 1; });
    const summary = Object.entries(counts).map(([source, n]) => `${n} × ${source}`).join(", ");
    note.innerHTML = `<strong>No shopping needed:</strong> ${summary} &mdash; delivered or eaten directly.`;
    body.appendChild(note);
  }
}

function weekRangeLabel() {
  const first = formatDate(plan.week[0].date);
  const last = formatDate(plan.week[plan.week.length - 1].date);
  return `${first} – ${last}`;
}

function exportHumanList() {
  const lines = [];
  lines.push("AutoEat — Shopping List");
  lines.push(`Week of ${weekRangeLabel()}`);
  lines.push("");

  if (shoppingList.ingredients.length) {
    lines.push("INGREDIENTS TO BUY");
    shoppingList.ingredients.forEach((i) => {
      lines.push(`- ${capitalize(i.display_name)}: ${i.to_buy_quantity} ${i.unit} (${formatMoney(i.estimated_price)})${i.have_quantity ? `  [have ${i.have_quantity} ${i.unit}]` : ""}`);
    });
    lines.push("");
  }

  if (shoppingList.packaged_items.length) {
    lines.push("PACKAGED / MARKET ITEMS");
    shoppingList.packaged_items.forEach((i) => {
      lines.push(`- ${i.name} × ${i.quantity} (${formatMoney(i.estimated_price)})`);
    });
    lines.push("");
  }

  lines.push(`Estimated total: ${formatMoney(shoppingList.total_estimated_cost)}`);

  if (shoppingList.no_shopping_needed.length) {
    lines.push("");
    lines.push("Not included (delivered/ordered directly, no shopping needed):");
    shoppingList.no_shopping_needed.forEach((i) => lines.push(`- ${i.name} (${i.source})`));
  }

  downloadFile("autoeat-shopping-list.txt", lines.join("\n"), "text/plain");
  toast("Downloaded shopping-list.txt");
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function exportInstacartFormat() {
  const ingredients = [
    ...shoppingList.ingredients
      .filter((i) => i.to_buy_quantity > 0)
      .map((i) => ({
        name: i.display_name,
        display_text: `${i.to_buy_quantity} ${i.unit} ${i.display_name}`,
        quantity: i.to_buy_quantity,
        unit: i.unit,
      })),
    ...shoppingList.packaged_items.map((i) => ({
      name: i.name,
      display_text: `${i.quantity} × ${i.unit}`,
      quantity: i.quantity,
      unit: i.unit,
    })),
  ];

  const payload = {
    title: "AutoEat Weekly Shopping List",
    week_of: plan.week[0].date,
    ingredients,
    instructions: [],
    note:
      "Shaped to match Instacart's Create Recipe Page API ingredient format " +
      "(docs.instacart.com/developer_platform_api). No live API call is made " +
      "yet — see backend_support.md for wiring this up with a real API key.",
  };

  downloadFile("autoeat-shopping-list-instacart.json", JSON.stringify(payload, null, 2), "application/json");
  toast("Downloaded shopping-list-instacart.json");
}

async function init() {
  renderShell("cost");
  renderStatRow();
  renderBudgetMeter(plan);
  renderChannelBars(plan);

  try {
    shoppingList = await postJSON("/api/shopping-list", { items: flattenWeekItems() });
    renderStatRow();
    renderShoppingList();
  } catch (err) {
    document.getElementById("shopping-list-body").innerHTML = "";
    document.getElementById("shopping-list-body").appendChild(el("div", "empty-state", err.message || "Couldn't build the shopping list."));
  }

  document.querySelectorAll(".export-icon").forEach((n) => (n.innerHTML = ICONS.download));
  document.getElementById("export-human-btn").addEventListener("click", () => shoppingList && exportHumanList());
  document.getElementById("export-instacart-btn").addEventListener("click", () => shoppingList && exportInstacartFormat());

  document.getElementById("loading").style.display = "none";
  document.getElementById("cost-root").style.display = "block";
}

if (plan) init();
