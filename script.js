const menuBtn = document.getElementById("menuBtn");
const siteNav = document.getElementById("siteNav");

if (menuBtn && siteNav) {
  menuBtn.addEventListener("click", () => {
    siteNav.classList.toggle("open");
  });

  siteNav.querySelectorAll("a").forEach((item) => {
    item.addEventListener("click", () => siteNav.classList.remove("open"));
  });
}

const currentPage = window.location.pathname.split("/").pop() || "index.html";
const isLoggedIn = localStorage.getItem("aisentinalAuth") === "1";
const protectedPages = new Set(["app.html", "dashboard.html", "detect.html", "detected.html", "faq.html"]);
const authPages = new Set(["login.html", "signup.html"]);

if (protectedPages.has(currentPage) && !isLoggedIn) {
  window.location.href = "login.html";
}

if (authPages.has(currentPage) && isLoggedIn) {
  window.location.href = "app.html";
}

const authRedirectForms = document.querySelectorAll("form[data-auth-redirect]");
authRedirectForms.forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    localStorage.setItem("aisentinalAuth", "1");
    const target = form.getAttribute("data-auth-redirect") || "app.html";
    window.location.href = target;
  });
});

const logoutButtons = document.querySelectorAll("[data-logout]");
logoutButtons.forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    localStorage.removeItem("aisentinalAuth");
    window.location.href = "index.html";
  });
});
