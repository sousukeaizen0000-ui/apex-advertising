const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
$("#year").textContent = new Date().getFullYear();

// Mobile navigation drawer
const drawer = $("#mobileDrawer"), backdrop = $("#drawerBackdrop"), toggle = $("#menuToggle");
function setDrawer(open){
  drawer.classList.toggle("open", open);
  backdrop.classList.toggle("show", open);
  drawer.setAttribute("aria-hidden", String(!open));
  toggle.setAttribute("aria-expanded", String(open));
  document.body.style.overflow = open ? "hidden" : "";
}
toggle.addEventListener("click", () => setDrawer(true));
$("#closeDrawer").addEventListener("click", () => setDrawer(false));
backdrop.addEventListener("click", () => setDrawer(false));
$$(".mobile-drawer a").forEach(a => a.addEventListener("click", () => setDrawer(false)));
document.addEventListener("keydown", e => { if(e.key === "Escape") setDrawer(false); });

// Smooth reveal animations, with reduced-motion support.
if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if(entry.isIntersecting){ entry.target.classList.add("visible"); observer.unobserve(entry.target); }
    });
  }, {threshold: .12});
  $$(".reveal").forEach(el => observer.observe(el));
} else { $$(".reveal").forEach(el => el.classList.add("visible")); }

// Community discovery filters and search.
let activeFilter = "all";
const searchInput = $("#searchInput");
function filterCards(){
  const query = searchInput.value.trim().toLowerCase();
  $$(".community-card").forEach(card => {
    const matchesCategory = activeFilter === "all" || card.dataset.category === activeFilter;
    const matchesText = card.dataset.name.toLowerCase().includes(query) || card.textContent.toLowerCase().includes(query);
    card.hidden = !(matchesCategory && matchesText);
  });
}
$$(".filter-button").forEach(button => button.addEventListener("click", () => {
  activeFilter = button.dataset.filter;
  $$(".filter-button").forEach(b => b.classList.toggle("active", b === button));
  filterCards();
}));
searchInput.addEventListener("input", filterCards);

// Saved community demo: stored only in this browser.
const STORAGE_KEY = "apexSavedCommunities";
function getSaved(){ try{return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")}catch{return []} }
function setSaved(items){ localStorage.setItem(STORAGE_KEY, JSON.stringify(items)); renderSaved(); }
function renderSaved(){
  const saved = getSaved();
  $("#savedCount").textContent = saved.length;
  const list = $("#savedList");
  list.innerHTML = "";
  if(!saved.length){ list.textContent = "No saved communities yet. Browse the listings above and tap “Save”."; }
  else saved.forEach(name => {
    const row = document.createElement("div"); row.className = "saved-entry";
    const label = document.createElement("span"); label.textContent = name;
    const remove = document.createElement("button"); remove.textContent = "Remove";
    remove.addEventListener("click", () => setSaved(getSaved().filter(item => item !== name)));
    row.append(label, remove); list.append(row);
  });
  $$("[data-save]").forEach(button => {
    const savedNow = saved.includes(button.dataset.save);
    button.textContent = savedNow ? "♥ Saved" : "♡ Save";
    button.setAttribute("aria-pressed", String(savedNow));
  });
}
$$("[data-save]").forEach(button => button.addEventListener("click", () => {
  const saved = getSaved(), name = button.dataset.save;
  setSaved(saved.includes(name) ? saved.filter(item => item !== name) : [...saved, name]);
}));
renderSaved();
