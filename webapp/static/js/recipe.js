function currentName() {
  const path = decodeURIComponent(window.location.pathname.replace(/^\/recipe\//, ""));
  return path;
}

function currentSource() {
  return new URLSearchParams(window.location.search).get("source") || "";
}

function macroTile(label, value, unit) {
  const tile = el("div", "stat-tile");
  tile.appendChild(el("div", "stat-label", label));
  tile.appendChild(el("div", "stat-value", `${Number(value).toFixed(label === "Calories" ? 0 : 1)}${unit}`));
  return tile;
}

function renderRecipe(data) {
  document.getElementById("recipe-eyebrow").textContent = data.category || "Recipe";
  document.getElementById("recipe-title").textContent = data.name;
  document.getElementById("recipe-price").textContent = formatMoney(data.per_serving.price);

  const badges = document.getElementById("recipe-badges");
  badges.appendChild(el("span", "badge badge-channel-home", "Home Cooking"));
  if (data.prep_minutes || data.cook_minutes) {
    badges.appendChild(el("span", "badge badge-neutral", `${ICONS.clock} ${data.prep_minutes}m prep · ${data.cook_minutes}m cook`));
  }

  const macroRow = document.getElementById("macro-row");
  macroRow.appendChild(macroTile("Calories", data.per_serving.calories, ""));
  macroRow.appendChild(macroTile("Protein", data.per_serving.protein, "g"));
  macroRow.appendChild(macroTile("Carbs", data.per_serving.carbs, "g"));
  macroRow.appendChild(macroTile("Fat", data.per_serving.fat, "g"));
  macroRow.appendChild(macroTile("Fiber", data.per_serving.fiber, "g"));

  document.getElementById("servings-hint").textContent = `Makes ${data.servings} serving${data.servings === 1 ? "" : "s"} total`;
  const ingCard = document.getElementById("ingredients-card");
  ingCard.style.display = "block";
  const list = document.getElementById("ingredients-list");
  data.ingredients.forEach((ing) => {
    const row = el("div", "ingredient-row");
    const haveTag = ing.have_quantity > 0
      ? `<span class="have-tag">have ${ing.have_quantity} ${ing.unit}${ing.to_buy_quantity === 0 ? " — fully covered" : `, buy ${ing.to_buy_quantity} ${ing.unit}`}</span>`
      : "";
    row.innerHTML = `
      <div><span style="text-transform:capitalize;">${ing.display_name}</span><br>${haveTag}</div>
      <div style="text-align:right;white-space:nowrap;">${ing.quantity} ${ing.unit}<br><span style="color:var(--text-muted);font-size:11.5px;">${formatMoney(ing.price_if_bought)} to buy</span></div>`;
    list.appendChild(row);
  });

  if (data.notes) {
    document.getElementById("notes-card").style.display = "block";
    document.getElementById("notes-text").textContent = data.notes;
  }

  document.getElementById("source-card").style.display = "block";
  document.getElementById("source-body").innerHTML = `
    <p style="margin:0 0 8px;">Cooked at home from a curated recipe in AutoEat's own database &mdash; no external recipe link is attached yet.</p>
    <p style="margin:0;font-size:12.5px;color:var(--text-muted);">Wiring in full step-by-step instructions or a source URL (e.g. from a recipe API) is tracked in backend_support.md.</p>`;
}

function renderProduct(data) {
  document.getElementById("recipe-eyebrow").textContent = data.category || data.channel;
  document.getElementById("recipe-title").textContent = itemLabel(data);
  document.title = `${itemLabel(data)} · AutoEat`;
  document.getElementById("recipe-price").textContent = formatMoney(data.per_serving.price);

  const badges = document.getElementById("recipe-badges");
  const badgeClass = CHANNEL_BADGE_CLASS[data.channel] || "badge-neutral";
  badges.appendChild(el("span", `badge ${badgeClass}`, data.channel));
  Object.entries(data.flags).forEach(([flag, on]) => {
    if (on) badges.appendChild(el("span", "badge badge-neutral", flag.replace("_", " ")));
  });

  const macroRow = document.getElementById("macro-row");
  macroRow.appendChild(macroTile("Calories", data.per_serving.calories, ""));
  macroRow.appendChild(macroTile("Protein", data.per_serving.protein, "g"));
  macroRow.appendChild(macroTile("Carbs", data.per_serving.carbs, "g"));
  macroRow.appendChild(macroTile("Fat", data.per_serving.fat, "g"));
  macroRow.appendChild(macroTile("Fiber", data.per_serving.fiber, "g"));

  if (data.notes) {
    document.getElementById("notes-card").style.display = "block";
    document.getElementById("notes-text").textContent = data.notes;
  }

  document.getElementById("source-card").style.display = "block";
  const locationLine = data.location ? `<p style="margin:0 0 8px;"><strong>Where:</strong> ${data.location} (${data.serving_unit})</p>` : "";
  // The full product title, brand and size included, is what's on the shelf.
  const productLine = data.display_name && data.display_name !== data.name
    ? `<p style="margin:0 0 8px;"><strong>Product:</strong> ${data.name}</p>` : "";
  const allergenLine = data.allergens.length ? `<p style="margin:0 0 8px;"><strong>Allergens:</strong> ${data.allergens.join(", ")}</p>` : "";
  document.getElementById("source-body").innerHTML = `
    ${productLine}
    ${locationLine}
    ${allergenLine}
    <p style="margin:0;font-size:12.5px;color:var(--text-muted);">This is a ${data.channel.toLowerCase()} item, not a home-cooked recipe &mdash; no ingredient breakdown applies. A real product link/menu URL isn't wired up yet, see backend_support.md.</p>`;
}

async function init() {
  renderShell("menu");
  document.querySelector(".back-icon").innerHTML = ICONS.back;
  document.getElementById("back-link").addEventListener("click", (e) => {
    e.preventDefault();
    if (document.referrer && document.referrer.includes(window.location.host)) {
      history.back();
    } else {
      window.location.href = "/menu";
    }
  });

  const name = currentName();
  const source = currentSource();
  document.title = `${name} · AutoEat`;

  try {
    const url = `/api/recipe/${encodeURIComponent(name)}${source ? `?source=${encodeURIComponent(source)}` : ""}`;
    const data = await getJSON(url);
    if (data.type === "recipe") renderRecipe(data);
    else renderProduct(data);
    document.getElementById("recipe-root").style.display = "block";
  } catch (err) {
    document.getElementById("error-text").textContent = err.message || "Couldn't find that item.";
    document.getElementById("recipe-error").style.display = "block";
  } finally {
    document.getElementById("loading").style.display = "none";
  }
}

init();
