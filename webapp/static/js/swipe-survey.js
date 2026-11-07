// Cold-start taste swipe survey: a quick, skippable card stack shown
// alongside the macro/budget form. onboarding.js calls window.swipeSurvey.finalize()
// right before it builds the /api/plan payload, reads .bonus off the result.
// A skipped, empty, or never-touched survey resolves to no bonus, so the
// plan behaves exactly like today's no-preference flow.
//
// Cards are adaptive: each one comes from POST /api/swipe-cards/next given
// every answer so far (see macro_optimizer/taste.py). While a card is on
// screen, the next card for both a like and a dislike is fetched in the
// background, so a swipe never waits on the network.
window.swipeSurvey = {
  bonus: null,
  submitted: false,
  finalize: async function () { return this.bonus; },
};

(function () {
  const section = document.getElementById("swipe-card");
  const stack = document.getElementById("swipe-stack");
  const loading = document.getElementById("swipe-loading");
  const progress = document.getElementById("swipe-progress");
  const actions = document.getElementById("swipe-actions");
  const doneEl = document.getElementById("swipe-done");
  const likeBtn = document.getElementById("swipe-like");
  const dislikeBtn = document.getElementById("swipe-dislike");
  const skipBtn = document.getElementById("swipe-skip");

  // One seed per survey keeps near-tie card choices repeatable, so a
  // prefetched card and a fresh request for the same answers agree.
  const seed = Math.floor(Math.random() * 2 ** 31);
  const SWIPE_THRESHOLD = 90; // px of drag that counts as a swipe

  let current = null; // the response currently on screen: {card, done, answered, total}
  let prefetch = null; // {liked: Promise, disliked: Promise} for the card on screen
  let skipped = false;
  let busy = false;
  const picks = [];

  function hideSection() {
    section.style.display = "none";
  }

  function fetchNext(answers) {
    return postJSON("/api/swipe-cards/next", { answers, seed });
  }

  function preloadImage(promise) {
    promise.then((r) => { if (r.card && r.card.image_url) new Image().src = r.card.image_url; }).catch(() => {});
    return promise;
  }

  function startPrefetch(card) {
    prefetch = {
      liked: preloadImage(fetchNext([...picks, { name: card.name, liked: true }])),
      disliked: preloadImage(fetchNext([...picks, { name: card.name, liked: false }])),
    };
    // Unused branches still settle; keep their rejections from surfacing.
    prefetch.liked.catch(() => {});
    prefetch.disliked.catch(() => {});
  }

  // Styled stand-in for a missing or broken photo: tinted by cuisine, with
  // the recipe's main ingredients (or the item's kind) as its caption.
  function placeholder(card) {
    const ph = card.placeholder || {};
    const node = el("div", "swipe-photo-placeholder");
    node.dataset.cuisine = ph.cuisine || "none";
    node.appendChild(el("span", "swipe-ph-eyebrow", "")).textContent =
      ph.cuisine && ph.cuisine !== "none" ? ph.cuisine : (ph.flavor || "");
    node.appendChild(el("span", "swipe-ph-hint", "")).textContent = ph.hint || "";
    return node;
  }

  function photo(card) {
    const frame = el("div", "swipe-card-photo");
    if (card.image_url) {
      const img = document.createElement("img");
      img.alt = card.title;
      img.decoding = "async";
      img.draggable = false;
      img.src = card.image_url;
      img.addEventListener("error", () => {
        frame.replaceChildren(placeholder(card));
      }, { once: true });
      frame.appendChild(img);
      if (card.image_credit) {
        frame.appendChild(el("span", "swipe-card-credit", "")).textContent = `Image: ${card.image_credit}`;
      }
    } else {
      frame.appendChild(placeholder(card));
    }
    return frame;
  }

  function finish() {
    stack.innerHTML = "";
    actions.style.display = "none";
    progress.textContent = "";
    doneEl.style.display = "block";
    submitPicks();
  }

  function render(response) {
    current = response;
    if (!response || response.done || !response.card) {
      finish();
      return;
    }
    const card = response.card;
    stack.innerHTML = "";
    const face = el("div", "swipe-card-face");
    face.appendChild(photo(card));
    const body = el("div", "swipe-card-body");
    body.appendChild(el("div", "swipe-card-title", "")).textContent = card.title;
    body.appendChild(el("div", "swipe-card-context", "")).textContent = card.context;
    face.appendChild(body);
    enableDrag(face);
    stack.appendChild(face);
    progress.textContent = `${response.answered + 1} / ${response.total}`;
    startPrefetch(card);
  }

  async function choose(liked) {
    if (busy || !current || !current.card) return;
    busy = true;
    const branch = liked ? prefetch.liked : prefetch.disliked;
    picks.push({ name: current.card.name, liked });
    const face = stack.querySelector(".swipe-card-face");
    if (face) face.classList.add(liked ? "is-leaving-right" : "is-leaving-left");
    try {
      const next = await branch.catch(() => fetchNext(picks));
      render(next);
    } catch (err) {
      finish(); // network gone: keep what we have rather than strand the user
    } finally {
      busy = false;
    }
  }

  // Pointer drag: follow the finger, commit past SWIPE_THRESHOLD, else snap back.
  function enableDrag(face) {
    let startX = null;
    face.addEventListener("pointerdown", (e) => {
      startX = e.clientX;
      face.setPointerCapture(e.pointerId);
      face.classList.add("is-dragging");
    });
    face.addEventListener("pointermove", (e) => {
      if (startX === null) return;
      const dx = e.clientX - startX;
      face.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`;
      face.dataset.lean = dx > 30 ? "like" : dx < -30 ? "dislike" : "";
    });
    const end = (e) => {
      if (startX === null) return;
      const dx = e.clientX - startX;
      startX = null;
      face.classList.remove("is-dragging");
      face.style.transform = "";
      face.dataset.lean = "";
      if (Math.abs(dx) >= SWIPE_THRESHOLD) choose(dx > 0);
    };
    face.addEventListener("pointerup", end);
    face.addEventListener("pointercancel", end);
  }

  // Posts whatever picks have been made so far. Safe to call more than once
  // (skip, finishing the stack, and the form-submit fallback can each reach
  // here) — a second call is a no-op once the survey is submitted.
  async function submitPicks() {
    if (skipped || window.swipeSurvey.submitted || picks.length === 0) {
      window.swipeSurvey.submitted = true;
      return window.swipeSurvey.bonus;
    }
    window.swipeSurvey.submitted = true;
    try {
      const body = await postJSON("/api/swipe-survey", { picks });
      window.swipeSurvey.bonus = body.preference_bonus || null;
    } catch (err) {
      window.swipeSurvey.bonus = null;
    }
    return window.swipeSurvey.bonus;
  }

  window.swipeSurvey.finalize = submitPicks;

  likeBtn.addEventListener("click", () => choose(true));
  dislikeBtn.addEventListener("click", () => choose(false));
  section.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); choose(true); }
    if (e.key === "ArrowLeft") { e.preventDefault(); choose(false); }
  });
  skipBtn.addEventListener("click", () => {
    skipped = true;
    window.swipeSurvey.submitted = true;
    window.swipeSurvey.bonus = null;
    hideSection();
  });

  fetchNext([])
    .then((response) => {
      if (!response.card) {
        hideSection();
        return;
      }
      loading.remove();
      actions.style.display = "flex";
      render(response);
    })
    .catch(() => {
      // Endpoint missing or failed — fall straight through to the
      // macro/budget form with no preference bonus, same as today.
      hideSection();
    });
})();
