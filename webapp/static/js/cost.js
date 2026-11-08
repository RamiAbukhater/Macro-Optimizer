const plan = loadPlan();
let shoppingList = null;

function renderStatRow() {
  const row = document.getElementById("stat-row");
  const weeklyBudget = plan.weekly_budget;
  const pct = weeklyBudget > 0 ? (plan.week_summary.total_cost / weeklyBudget) * 100 : 0;
  const tiles = [
    { label: "Food you eat this week", value: formatMoney(plan.week_summary.total_cost), sub: `vs ${formatMoney(weeklyBudget)}/week budget` },
    { label: "Avg daily cost", value: formatMoney(plan.week_summary.avg_daily_cost), sub: `${formatMoney(weeklyBudget / PLAN_DAYS)}/day to stay on budget` },
    { label: "Budget utilization", value: `${pct.toFixed(0)}%`, sub: pct <= 100 ? "within budget" : "over budget" },
    {
      label: "At the register",
      value: shoppingList ? formatMoney(shoppingList.total_estimated_cost) : "—",
      sub: shoppingList ? `whole packages, minus your fridge` : "whole packages",
    },
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

function fmtQty(n) {
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function plural(n, word) {
  return `${fmtQty(n)} ${word}${Number(n) === 1 ? "" : "s"}`;
}

/** One shopping line as display strings: title, sub, buy, each, leftover. */
function describeLine(i) {
  if (i.ingredient) {
    const name = capitalize(i.display_name);
    const uses = `${fmtQty(i.needed_quantity)} ${i.unit} this week` + (i.have_quantity ? ` · ${fmtQty(i.have_quantity)} ${i.unit} in fridge` : "");
    if (i.packages !== null && i.packages !== undefined) {
      return {
        title: i.title || name, sub: `${name} · ${uses}`,
        buy: i.packages ? `${i.packages} × ${i.package_size}` : "Have enough",
        each: i.packages ? formatMoney(i.package_price) : "",
        leftover: i.packages ? `${fmtQty(i.leftover_quantity)} ${i.unit}` : "—",
      };
    }
    return {
      title: name, sub: `${uses} · loose, no package size known`,
      buy: i.to_buy_quantity ? `${fmtQty(i.to_buy_quantity)} ${i.unit}` : "Have enough", each: "", leftover: "—",
    };
  }
  const uses = `${plural(i.quantity, "serving")} this week` + (i.have_servings ? ` · ${fmtQty(i.have_servings)} in fridge` : "");
  if (i.packages !== null && i.packages !== undefined) {
    return {
      title: i.name, sub: `${uses} · ${fmtQty(i.servings_per_package)} per package`,
      buy: i.packages ? `${i.packages} × ${i.package_size || "package"}` : "Have enough",
      each: i.packages ? formatMoney(i.package_price) : "",
      leftover: i.packages ? plural(i.leftover_servings, "serving") : "—",
    };
  }
  return { title: i.name, sub: uses, buy: `${fmtQty(i.to_buy_servings)} × ${i.unit}`, each: "", leftover: "—" };
}

function needsBuying(i) {
  return Boolean(i.packages || i.estimated_price);
}

async function setBought(payload, checkbox) {
  if (checkbox) checkbox.disabled = true;
  try {
    shoppingList = await postJSON("/api/shopping-list/bought", payload);
    renderStatRow();
    renderShoppingList();
    if (payload.all) toast(payload.bought ? "Everything marked bought and added to your fridge" : "Unmarked everything");
    else toast(payload.bought ? "Added to your fridge" : "Taken back out of your fridge");
  } catch (err) {
    toast(err.message || "Couldn't update that line.");
    if (checkbox) { checkbox.checked = !checkbox.checked; checkbox.disabled = false; }
  }
}

function renderSection(body, heading, lines) {
  if (!lines.length) return;
  body.appendChild(el("h3", "", heading));
  const wrap = el("div", "table-wrap");
  const table = el("table", "data-table shopping-table");
  table.innerHTML = `<thead><tr><th class="check-col"><span class="sr-only">Bought</span></th><th>Item</th><th class="num">Buy</th><th class="num">Each</th><th class="num">Total</th><th class="num">Left over</th></tr></thead>`;
  const tbody = el("tbody");
  lines.forEach((i) => {
    const d = describeLine(i);
    const tr = el("tr", i.bought ? "is-bought" : "");
    const id = `buy-${i.key.replace(/[^a-z0-9]/gi, "-")}`;
    const enabled = needsBuying(i) || i.bought;
    tr.innerHTML = `
      <td class="check-col"><input type="checkbox" id="${id}" ${i.bought ? "checked" : ""} ${enabled ? "" : "disabled"} aria-label="Bought ${escapeHtml(d.title)}"></td>
      <td><label for="${id}" class="line-title">${escapeHtml(d.title)}</label><div class="line-sub">${escapeHtml(d.sub)}</div></td>
      <td class="num">${escapeHtml(d.buy)}</td>
      <td class="num">${d.each}</td>
      <td class="num">${formatMoney(i.estimated_price)}</td>
      <td class="num">${escapeHtml(d.leftover)}</td>`;
    const box = tr.querySelector("input");
    box.addEventListener("change", () => setBought({ key: i.key, bought: box.checked }, box));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  body.appendChild(wrap);
}

function renderShoppingList() {
  const body = document.getElementById("shopping-list-body");
  body.innerHTML = "";
  const all = [...shoppingList.packaged_items, ...shoppingList.ingredients];
  const toBuy = all.filter((i) => needsBuying(i) || i.bought);
  const done = all.filter((i) => i.bought).length;

  const allBtn = document.getElementById("mark-all-btn");
  const everything = toBuy.length > 0 && toBuy.every((i) => i.bought);
  allBtn.textContent = everything ? "Unmark all" : "Mark all bought";
  allBtn.disabled = toBuy.length === 0;
  allBtn.onclick = () => setBought({ all: true, bought: !everything });
  document.getElementById("bought-progress").textContent = toBuy.length
    ? `${done} of ${toBuy.length} bought · eating ${formatMoney(shoppingList.total_eaten_cost)} of it this week`
    : "";

  if (all.length === 0) {
    body.appendChild(el("div", "empty-state", `${ICONS.empty}<div>Nothing to shop for this week &mdash; every meal is delivered, ordered, or already in your fridge.</div>`));
    return;
  }

  renderSection(body, "Store products", shoppingList.packaged_items);
  renderSection(body, "Recipe ingredients", shoppingList.ingredients);

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
    shoppingList.ingredients.filter(needsBuying).forEach((i) => {
      const d = describeLine(i);
      lines.push(`- [${i.bought ? "x" : " "}] ${d.title}: ${d.buy} (${formatMoney(i.estimated_price)})`);
    });
    lines.push("");
  }

  if (shoppingList.packaged_items.length) {
    lines.push("PACKAGED / MARKET ITEMS");
    shoppingList.packaged_items.filter(needsBuying).forEach((i) => {
      const d = describeLine(i);
      lines.push(`- [${i.bought ? "x" : " "}] ${d.title}: ${d.buy} (${formatMoney(i.estimated_price)}), ${d.leftover} left over`);
    });
    lines.push("");
  }

  lines.push(`At the register: ${formatMoney(shoppingList.total_estimated_cost)}`);
  lines.push(`Food eaten this week: ${formatMoney(shoppingList.total_eaten_cost)}`);

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
    ...shoppingList.packaged_items.filter(needsBuying).map((i) => ({
      name: i.name,
      display_text: describeLine(i).buy,
      quantity: i.packages || i.to_buy_servings,
      unit: i.packages ? "package" : i.unit,
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
    shoppingList = await getJSON("/api/shopping-list");
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
