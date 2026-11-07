/* Shared shell (sidebar), plan storage, and small DOM/API helpers used by every page. */

const ICONS = {
  overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 6h16M4 12h16M4 18h10" stroke-linecap="round"/></svg>',
  cost: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9h18l-1.5 10.5a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5L3 9Z"/><path d="M8 9V6a4 4 0 0 1 8 0v3" stroke-linecap="round"/></svg>',
  fridge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M5 10h14M8 5.5v2M8 13v2" stroke-linecap="round"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 20h9" stroke-linecap="round"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"/></svg>',
  shuffle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M17 3l4 4-4 4M3 7h5c1.5 0 2.3.7 3.2 2M21 17h-5c-1.5 0-2.3-.7-3.2-2M17 21l4-4-4-4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  swap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 3v14M7 17l-3-3M7 17l3-3M17 21V7M17 7l3 3M17 7l-3 3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v13M7 11l5 5 5-5M4 20h16" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14" stroke-linecap="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7m2 0v12.5A1.5 1.5 0 0 1 15.5 21h-7A1.5 1.5 0 0 1 7 19.5V7h10Z" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  mark: '<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><circle class="mark-plate" cx="16" cy="16" r="15"/><path class="mark-wedge" d="M16 16V4.5a11.5 11.5 0 0 1 9.96 17.25Z"/><circle class="mark-hole" cx="16" cy="16" r="4"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" stroke-linejoin="round"/></svg>',
  empty: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" stroke-linecap="round"/></svg>',
};

const NAV_ITEMS = [
  { key: "overview", href: "/dashboard", label: "Overview", icon: "overview" },
  { key: "menu", href: "/menu", label: "Menu", icon: "menu" },
  { key: "cost", href: "/cost", label: "Cost", icon: "cost" },
  { key: "fridge", href: "/fridge", label: "Fridge", icon: "fridge" },
  { key: "settings", href: "/settings", label: "Settings", icon: "settings" },
];

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

/** "2026-10-10" -> { weekday: "Sat", monthDay: "Oct 10" } for the ticket headers. */
function dateParts(iso) {
  const d = new Date(iso + "T00:00:00");
  return {
    weekday: d.toLocaleDateString(undefined, { weekday: "short" }),
    monthDay: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
  };
}

function formatMoney(n) {
  return `$${Number(n).toFixed(2)}`;
}

function renderShell(activeKey) {
  const mount = document.getElementById("sidebar-mount");
  if (!mount) return;
  const nav = NAV_ITEMS.map(
    (item, i) => `<a href="${item.href}" class="${item.key === activeKey ? "active" : ""}"><span class="nav-num">0${i + 1}</span>${ICONS[item.icon]}<span class="nav-label">${item.label}</span></a>`
  ).join("");

  mount.innerHTML = `
    <a class="sidebar-brand" href="/dashboard">${ICONS.mark}AutoEat</a>
    <nav class="sidebar-nav">${nav}</nav>
    <div class="sidebar-foot">
      <a href="#" id="start-over-link" title="Start over">${ICONS.back}<span>Start over</span></a>
      <a href="#" id="logout-link" title="Log out">${ICONS.logout}<span>Log out</span></a>
    </div>
  `;

  document.getElementById("start-over-link").addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      await postJSON("/api/plan/reset", {});
    } catch (err) {
      /* fall through to the redirect either way */
    }
    window.location.href = "/";
  });

  document.getElementById("logout-link").addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      await postJSON("/api/auth/logout", {});
    } catch (err) {
      /* fall through to the redirect either way */
    }
    window.location.href = "/login";
  });
}

function toast(message) {
  let node = document.getElementById("app-toast");
  if (!node) {
    node = el("div", "toast");
    node.id = "app-toast";
    document.body.appendChild(node);
  }
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node._hideTimer);
  node._hideTimer = setTimeout(() => node.classList.remove("show"), 2400);
}

const PLAN_KEY = "macroPlan";
const PLAN_DAYS = 7;
const WEEKS_PER_MONTH = 52 / 12;

/** Plans saved before the switch to a weekly budget carry a DAILY `budget`
 * instead of `weekly_budget`; read those as budget * 7 (never as $X/week)
 * and rewrite the field so every page only ever sees `weekly_budget`. */
function normalizePlanBudget(plan) {
  if (plan && plan.weekly_budget == null && plan.budget != null) {
    plan.weekly_budget = Number(plan.budget) * PLAN_DAYS;
    delete plan.budget;
  }
  return plan;
}

/** "≈ $758/month" for a weekly amount. */
function monthlyHint(weekly) {
  const monthly = Math.round(Number(weekly) * WEEKS_PER_MONTH);
  return `≈ $${monthly.toLocaleString("en-US")}/month`;
}

function loadPlan(redirect = true) {
  const raw = sessionStorage.getItem(PLAN_KEY);
  if (raw) {
    try {
      return normalizePlanBudget(JSON.parse(raw));
    } catch (e) {
      /* fall through to the empty-sessionStorage handling below */
    }
  }

  if (redirect) {
    // sessionStorage is empty — a new tab, cleared storage, or a session
    // cookie that outlived the browser session sessionStorage lives in.
    // The account may still have a real plan server-side (see
    // GET /api/plan), so check there before giving up and bouncing back to
    // onboarding: adopt it into sessionStorage and reload rather than
    // discarding a plan that still exists.
    getJSON("/api/plan")
      .then((serverPlan) => {
        savePlan(serverPlan);
        window.location.reload();
      })
      .catch(() => {
        window.location.href = "/";
      });
  }
  return null;
}

function savePlan(plan) {
  sessionStorage.setItem(PLAN_KEY, JSON.stringify(plan));
}

/** Rebuild the {sex, age, ...} + weekly_budget/cook_time/filters payload the plan endpoints expect,
 * from the profile inputs stashed alongside the plan at onboarding time. */
function planRequestBase(plan) {
  return {
    ...plan.profile_input,
    weekly_budget: plan.weekly_budget,
    cook_time: plan.cook_time,
    // Per-day minutes (Mon..Sun). Older saved plans only carry cook_time;
    // the server expands that to 7 equal days when cook_schedule is absent.
    ...(Array.isArray(plan.cook_schedule) ? { cook_schedule: plan.cook_schedule } : {}),
    filters: plan.filters || {},
  };
}

async function apiJSON(url, options = {}) {
  const resp = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {
    /* no body */
  }
  if (!resp.ok) {
    const message = (body && body.error) || `Request to ${url} failed (${resp.status})`;
    throw new Error(message);
  }
  return body;
}

function postJSON(url, data) {
  return apiJSON(url, { method: "POST", body: JSON.stringify(data) });
}

function getJSON(url) {
  return apiJSON(url);
}

function deleteJSON(url) {
  return apiJSON(url, { method: "DELETE" });
}

/** Recompute channel_totals / week_summary / today client-side after a
 * single day's items change (replace-item only returns that one day). */
function recomputeAggregates(plan) {
  const channelTotals = {};
  let totalCost = 0;
  const infeasibleDays = [];

  plan.week.forEach((day) => {
    if (!day.feasible) infeasibleDays.push(day.date);
    totalCost += day.achieved.cost;
    day.items.forEach((item) => {
      const bucket = channelTotals[item.channel] || (channelTotals[item.channel] = { cost: 0, items: 0 });
      bucket.cost += item.total_cost;
      bucket.items += 1;
    });
  });

  Object.values(channelTotals).forEach((b) => {
    b.cost = Math.round(b.cost * 100) / 100;
  });

  plan.channel_totals = channelTotals;
  plan.week_summary = {
    total_cost: Math.round(totalCost * 100) / 100,
    avg_daily_cost: Math.round((totalCost / plan.week.length) * 100) / 100,
    days_planned: plan.week.length,
    infeasible_days: infeasibleDays,
  };
  plan.today = plan.week[0];
}

function renderBudgetMeter(plan) {
  const weeklyBudget = plan.weekly_budget;
  const pct = weeklyBudget > 0 ? Math.min(100, (plan.week_summary.total_cost / weeklyBudget) * 100) : 0;
  document.getElementById("budget-meter-value").textContent =
    `${formatMoney(plan.week_summary.total_cost)} / ${formatMoney(weeklyBudget)} (${pct.toFixed(0)}%)`;
  document.getElementById("budget-meter-fill").style.width = `${pct}%`;
}

function renderChannelBars(plan) {
  const container = document.getElementById("channel-bars");
  container.innerHTML = "";
  const entries = Object.entries(plan.channel_totals).sort((a, b) => b[1].cost - a[1].cost);
  if (entries.length === 0) {
    container.appendChild(el("div", "empty-state", "No items were selected this week."));
    return;
  }
  const totalCost = entries.reduce((sum, [, v]) => sum + v.cost, 0);

  // One stacked bar for the split, then a receipt-style line per channel.
  const bar = el("div", "mix-bar");
  entries.forEach(([channel, v]) => {
    const seg = el("div", "mix-seg");
    seg.style.flex = `${Math.max(v.cost, 0.01)} 0 0`;
    seg.style.background = CHANNEL_COLOR[channel] || "var(--series-1)";
    seg.title = `${channel}: ${formatMoney(v.cost)}`;
    bar.appendChild(seg);
  });
  container.appendChild(bar);

  entries.forEach(([channel, v]) => {
    const share = totalCost > 0 ? (v.cost / totalCost) * 100 : 0;
    const row = el("div", "leader-row");
    row.innerHTML = `
      <span class="leader-name"><span class="chan-dot" style="background:${CHANNEL_COLOR[channel] || "var(--series-1)"};"></span>${channel}</span>
      <span class="leader-dots"></span>
      <span class="leader-value">${v.items} item${v.items === 1 ? "" : "s"} · ${share.toFixed(0)}% · <strong>${formatMoney(v.cost)}</strong></span>`;
    container.appendChild(row);
  });

  if (plan.week_summary.infeasible_days.length > 0) {
    container.appendChild(el("p", "mix-note", `${plan.week_summary.infeasible_days.length} day(s) this week were best-effort (couldn't fully meet goals within budget/time): ${plan.week_summary.infeasible_days.join(", ")}.`));
  }
}

function recipeHref(item) {
  return `/recipe/${encodeURIComponent(item.name)}?source=${encodeURIComponent(item.source)}`;
}

function downloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
