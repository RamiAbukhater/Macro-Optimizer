let ingredientDb = [];
let pantryItems = [];

function ingredientUnit(key) {
  const ing = ingredientDb.find((i) => i.key === key);
  if (ing) return ing.unit;
  const tracked = pantryItems.find((i) => i.ingredient === key);
  return tracked ? tracked.unit : "";
}

async function loadIngredients() {
  const resp = await getJSON("/api/ingredients");
  ingredientDb = resp.ingredients;
  const select = document.getElementById("ingredient-select");
  select.innerHTML = ingredientDb
    .map((i) => `<option value="${i.key}" style="text-transform:capitalize;">${i.display_name}</option>`)
    .join("");
  document.getElementById("unit-label").textContent = `(${ingredientUnit(select.value)})`;
}

function daysLabel(days) {
  if (days === null || days === undefined) return "";
  if (days < 0) return `Past use-by ${Math.abs(days)}d`;
  if (days === 0) return "Use today";
  if (days === 1) return "Use by tomorrow";
  return `Use within ${days}d`;
}

function qtyLabel(item) {
  const n = Number(item.quantity).toLocaleString(undefined, { maximumFractionDigits: 1 });
  return item.unit === "servings" ? `${n} serving${item.quantity === 1 ? "" : "s"}` : `${n} ${item.unit}`;
}

function rowClass(days) {
  if (days === null || days === undefined) return "";
  if (days < 0) return "expired";
  if (days <= 5) return "expiring-soon";
  return "";
}

function renderExpiringBanner(pantryData) {
  const banner = document.getElementById("expiring-banner");
  banner.innerHTML = "";
  if (!pantryData.expiring_soon.length) return;
  const names = pantryData.expiring_soon.map((n) => n.replace(/_/g, " ")).join(", ");
  banner.appendChild(el("div", "callout", `<strong>Use soon</strong><span>${names}. Your next plan works these in first.</span>`));
}

function renderTable() {
  const wrap = document.getElementById("pantry-table-wrap");
  document.getElementById("item-count-hint").textContent = `${pantryItems.length} item${pantryItems.length === 1 ? "" : "s"}`;

  if (pantryItems.length === 0) {
    wrap.innerHTML = "";
    wrap.appendChild(el("div", "empty-state", `${ICONS.empty}<div>Nothing tracked yet. Check items off on the shopping list as you buy them, or add what's already here above.</div>`));
    wrap.style.padding = "0 24px 24px";
    return;
  }
  wrap.style.padding = "0";

  const table = el("table", "data-table");
  table.innerHTML = `<thead><tr><th>Item</th><th class="num">Left</th><th>Bought</th><th>Use by</th><th></th></tr></thead>`;
  const tbody = el("tbody");
  pantryItems.forEach((item) => {
    const tr = el("tr", rowClass(item.days_until_expiry));
    tr.innerHTML = `
      <td${item.kind === "product" ? "" : ' style="text-transform:capitalize;"'}>${item.display_name}</td>
      <td class="num">${qtyLabel(item)}</td>
      <td>${item.purchased_on ? `<span class="num">${formatDate(item.purchased_on)}</span>` : '<span style="color:var(--text-muted);">—</span>'}${item.from_shopping ? ' <span class="badge badge-good" style="margin-left:6px;">From shopping</span>' : ""}</td>
      <td>${item.expires_on ? `<span class="num">${formatDate(item.expires_on)}</span> <span class="badge ${item.days_until_expiry <= 2 ? "badge-warn" : "badge-neutral"}" style="margin-left:6px;">${daysLabel(item.days_until_expiry)}</span>` : '<span style="color:var(--text-muted);">No date</span>'}</td>
      <td></td>`;
    const actionsCell = tr.lastElementChild;
    const editBtn = el("button", "btn btn-ghost btn-sm", "Edit");
    editBtn.addEventListener("click", () => populateForm(item));
    const delBtn = el("button", "item-replace-btn", ICONS.trash);
    delBtn.title = `Remove ${item.display_name}`;
    delBtn.addEventListener("click", () => handleDelete(item));
    actionsCell.style.display = "flex";
    actionsCell.style.gap = "4px";
    actionsCell.style.justifyContent = "flex-end";
    actionsCell.appendChild(editBtn);
    actionsCell.appendChild(delBtn);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.innerHTML = "";
  wrap.appendChild(table);
}

async function refreshPantry() {
  const data = await getJSON("/api/pantry");
  pantryItems = data.items;
  renderExpiringBanner(data);
  renderTable();
}

function populateForm(item) {
  const select = document.getElementById("ingredient-select");
  if (![...select.options].some((o) => o.value === item.ingredient)) {
    // Store products and custom items aren't in the curated dropdown; add them so they can be edited.
    const opt = document.createElement("option");
    opt.value = item.ingredient;
    opt.textContent = item.display_name;
    select.appendChild(opt);
  }
  select.value = item.ingredient;
  document.getElementById("quantity-input").value = item.quantity;
  document.getElementById("expiry-input").value = item.expires_on || "";
  document.getElementById("unit-label").textContent = `(${item.unit})`;
  document.getElementById("quantity-input").focus();
}

async function handleDelete(item) {
  try {
    await deleteJSON(`/api/pantry/${encodeURIComponent(item.ingredient)}`);
    toast(`Removed ${item.display_name}`);
    await refreshPantry();
  } catch (err) {
    toast(err.message || "Couldn't remove that item.");
  }
}

async function handleSubmit(e) {
  e.preventDefault();
  const ingredient = document.getElementById("ingredient-select").value;
  const quantity = Number(document.getElementById("quantity-input").value);
  const expires_on = document.getElementById("expiry-input").value || null;

  const btn = document.getElementById("pantry-submit");
  btn.disabled = true;
  try {
    await postJSON("/api/pantry", { ingredient, quantity, expires_on, unit: ingredientUnit(ingredient) || undefined });
    toast("Saved to fridge");
    document.getElementById("pantry-form").reset();
    document.getElementById("unit-label").textContent = `(${ingredientUnit(document.getElementById("ingredient-select").value)})`;
    await refreshPantry();
  } catch (err) {
    toast(err.message || "Couldn't save that item.");
  } finally {
    btn.disabled = false;
  }
}

async function init() {
  renderShell("fridge");
  document.querySelector(".pantry-plus-icon").innerHTML = ICONS.plus;
  await loadIngredients();
  document.getElementById("ingredient-select").addEventListener("change", (e) => {
    document.getElementById("unit-label").textContent = `(${ingredientUnit(e.target.value)})`;
  });
  document.getElementById("pantry-form").addEventListener("submit", handleSubmit);
  await refreshPantry();
}

init();
