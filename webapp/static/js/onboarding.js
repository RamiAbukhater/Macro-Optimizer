const goalCards = document.querySelectorAll(".goal-card");
let selectedGoal = "maintain";

goalCards.forEach((card) => {
  card.addEventListener("click", () => {
    goalCards.forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    selectedGoal = card.dataset.value;
  });
});

const form = document.getElementById("profile-form");
const errorBanner = document.getElementById("error-banner");
const submitBtn = document.getElementById("submit-btn");

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.style.display = "block";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBanner.style.display = "none";
  submitBtn.disabled = true;
  submitBtn.textContent = "Building your plan…";

  const payload = {
    sex: document.getElementById("sex").value,
    age: Number(document.getElementById("age").value),
    height_ft: Number(document.getElementById("height_ft").value),
    height_in: Number(document.getElementById("height_in").value),
    weight_lbs: Number(document.getElementById("weight_lbs").value),
    activity: document.getElementById("activity").value,
    goal: selectedGoal,
    budget: Number(document.getElementById("budget").value),
    cook_time: document.getElementById("cook_time").value,
    filters: {
      vegetarian: document.getElementById("f-vegetarian").checked,
      vegan: document.getElementById("f-vegan").checked,
      halal: document.getElementById("f-halal").checked,
      gluten_free: document.getElementById("f-gluten_free").checked,
      no_pork: document.getElementById("f-no_pork").checked,
    },
  };

  try {
    const resp = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await resp.json();

    if (!resp.ok) {
      showError(body.error || "Something went wrong building your plan.");
      submitBtn.disabled = false;
      submitBtn.textContent = "Build my plan";
      return;
    }

    sessionStorage.setItem("macroPlan", JSON.stringify(body));
    window.location.href = "/dashboard";
  } catch (err) {
    showError("Could not reach the server. Is the Flask app running?");
    submitBtn.disabled = false;
    submitBtn.textContent = "Build my plan";
  }
});
