// Login/signup form handlers, shared by login.html and signup.html (each
// page only has one of the two forms, so only one handler ever attaches).
const errorBanner = document.getElementById("error-banner");

function showAuthError(message) {
  errorBanner.textContent = message;
  errorBanner.style.display = "block";
}

async function handleAuthSubmit(form, url, buildPayload) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorBanner.style.display = "none";
    const submitBtn = document.getElementById("submit-btn");
    submitBtn.disabled = true;

    try {
      await postJSON(url, buildPayload());
      window.location.href = "/";
    } catch (err) {
      showAuthError(err.message || "Something went wrong. Is the Flask app running?");
      submitBtn.disabled = false;
    }
  });
}

const loginForm = document.getElementById("login-form");
if (loginForm) {
  handleAuthSubmit(loginForm, "/api/auth/login", () => ({
    identifier: document.getElementById("identifier").value,
    password: document.getElementById("password").value,
  }));
}

const signupForm = document.getElementById("signup-form");
if (signupForm) {
  handleAuthSubmit(signupForm, "/api/auth/signup", () => ({
    email: document.getElementById("email").value,
    username: document.getElementById("username").value,
    password: document.getElementById("password").value,
  }));
}
