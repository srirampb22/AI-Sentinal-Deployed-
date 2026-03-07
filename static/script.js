const menuBtn = document.getElementById("menuBtn");
const siteNav = document.getElementById("siteNav");
const themeToggle = document.getElementById("themeToggle");
const THEME_KEY = "aisentinalTheme";
const hasLogoutControl = document.querySelector("[data-logout]") !== null;

function applyTheme(theme) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", normalized);
  localStorage.setItem(THEME_KEY, normalized);
  if (themeToggle) {
    const isDark = normalized === "dark";
    themeToggle.setAttribute("aria-pressed", isDark ? "true" : "false");
    themeToggle.setAttribute("title", isDark ? "Switch to bright mode" : "Switch to dark mode");
  }
}

if (hasLogoutControl) {
  const savedTheme = localStorage.getItem(THEME_KEY) || "light";
  applyTheme(savedTheme);
} else {
  applyTheme("light");
}

if (menuBtn && siteNav) {
  menuBtn.addEventListener("click", () => {
    siteNav.classList.toggle("open");
  });

  siteNav.querySelectorAll("a").forEach((item) => {
    item.addEventListener("click", () => siteNav.classList.remove("open"));
  });
}

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const current = localStorage.getItem(THEME_KEY) || "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
}

const logoutButtons = document.querySelectorAll("[data-logout]");
const csrfMeta = document.querySelector("meta[name='csrf-token']");
const csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";
logoutButtons.forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    applyTheme("light");
    const form = document.createElement("form");
    form.method = "POST";
    form.action = "/logout";
    if (csrfToken) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = csrfToken;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.submit();
  });
});
