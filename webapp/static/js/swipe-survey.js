// Cold-start taste swipe survey: a quick, skippable card stack shown
// alongside the macro/budget form. onboarding.js calls window.swipeSurvey.finalize()
// right before it builds the /api/plan payload, reads .bonus off the result.
// A skipped, empty, or never-touched survey resolves to no bonus, so the
// plan behaves exactly like today's no-preference flow.
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

  let cards = [];
  let index = 0;
  let skipped = false;
  const picks = [];

  function hideSection() {
    section.style.display = "none";
  }

  function itemMetaLine(card) {
    return `${card.calories.toFixed(0)} kcal · ${card.protein.toFixed(0)}g protein · $${card.price.toFixed(2)}`;
  }

  function renderCard() {
    stack.innerHTML = "";
    if (index >= cards.length) {
      actions.style.display = "none";
      progress.textContent = "";
      doneEl.style.display = "block";
      submitPicks();
      return;
    }
    const card = cards[index];
    const face = el("div", "swipe-card-face");
    face.appendChild(el("div", "swipe-card-name", itemLabel(card)));
    face.appendChild(el("div", "swipe-card-source", card.source));
    face.appendChild(el("div", "swipe-card-meta", itemMetaLine(card)));
    stack.appendChild(face);
    progress.textContent = `${index + 1} / ${cards.length}`;
  }

  function choose(liked) {
    if (index >= cards.length) return;
    picks.push({ name: cards[index].name, liked });
    index += 1;
    renderCard();
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
  skipBtn.addEventListener("click", () => {
    skipped = true;
    window.swipeSurvey.submitted = true;
    window.swipeSurvey.bonus = null;
    hideSection();
  });

  getJSON("/api/swipe-cards")
    .then((body) => {
      cards = body.cards || [];
      if (cards.length === 0) {
        hideSection();
        return;
      }
      loading.remove();
      actions.style.display = "flex";
      renderCard();
    })
    .catch(() => {
      // No swipe-cards endpoint yet, or it failed — fall straight through to
      // the macro/budget form with no preference bonus, same as today.
      hideSection();
    });
})();
