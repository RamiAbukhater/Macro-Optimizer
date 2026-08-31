let ingredientDb = [];
let pantryItems = [];

function ingredientUnit(key) {
  const ing = ingredientDb.find((i) => i.key === key);
  return ing ? ing.unit : "";
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
  if (days < 0) return `Expired ${Math.abs(days)}d ago`;
  if (days === 0) return "Expires today";
  return `${days}d left`;
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
  const card = el("div", "card");
  card.style.background = "var(--warning-soft)";
  card.style.borderColor = "color-mix(in srgb, var(--warning) 45%, transparent)";
  card.style.display = "flex";
  card.style.gap = "10px";
  card.style.alignItems = "center";
  card.innerHTML = `<strong style="color:var(--warning);">Expiring soon:</strong> <span style="color:var(--text-primary);">${names}</span>`;
  banner.appendChild(card);
}

function renderTable() {
  const wrap = document.getElementById("pantry-table-wrap");
  document.getElementById("item-count-hint").textContent = `${pantryItems.length} item${pantryItems.length === 1 ? "" : "s"}`;

  if (pantryItems.length === 0) {
    wrap.innerHTML = "";
    wrap.appendChild(el("div", "empty-state", `${ICONS.empty}<div>Nothing tracked yet. Add what's in your fridge above to start saving on recipe cost.</div>`));
    wrap.style.padding = "0 22px 22px";
    return;
  }
  wrap.style.padding = "0";

  const table = el("table", "data-table");
  table.innerHTML = `<thead><tr><th>Ingredient</th><th class="num">Quantity</th><th>Expiry</th><th></th></tr></thead>`;
  const tbody = el("tbody");
  pantryItems.forEach((item) => {
    const tr = el("tr", rowClass(item.days_until_expiry));
    tr.innerHTML = `
      <td style="text-transform:capitalize;">${item.display_name}</td>
      <td class="num">${item.quantity} ${item.unit}</td>
      <td>${item.expires_on ? `${item.expires_on} <span class="badge ${item.days_until_expiry < 0 ? "badge-warn" : item.days_until_expiry <= 5 ? "badge-warn" : "badge-neutral"}" style="margin-left:6px;">${daysLabel(item.days_until_expiry)}</span>` : '<span style="color:var(--text-muted);">No expiry set</span>'}</td>
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
  document.getElementById("ingredient-select").value = item.ingredient;
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
    await postJSON("/api/pantry", { ingredient, quantity, expires_on });
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
