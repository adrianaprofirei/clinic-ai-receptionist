/* Landing page behaviour: mobile menu, click-to-load map, live calculator. */
(function () {
  "use strict";

  // --- mobile menu -----------------------------------------------------------------
  const toggle = document.querySelector("[data-menu-toggle]");
  const menu = document.getElementById("meniu-mobil");
  if (toggle && menu) {
    const setMenu = (open) => {
      menu.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Închide meniul" : "Deschide meniul");
    };
    toggle.addEventListener("click", () => setMenu(menu.hidden));
    menu.addEventListener("click", (event) => {
      if (event.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hidden) {
        setMenu(false);
        toggle.focus();
      }
    });
  }

  // --- map: nothing is loaded from OpenStreetMap until the visitor asks ---------------
  document.querySelectorAll("[data-map]").forEach((slot) => {
    const button = slot.querySelector("[data-map-load]");
    if (!button) return;
    button.addEventListener("click", () => {
      const frame = document.createElement("iframe");
      frame.src = slot.dataset.mapSrc;
      frame.title = "Harta zonei (adresă fictivă, pentru demo)";
      frame.loading = "lazy";
      frame.referrerPolicy = "no-referrer";
      slot.replaceChildren(frame);
      slot.classList.add("map-slot--loaded");
    });
  });

  // --- "Cum am calculat": open on large screens, collapsed on phones -------------------
  const formula = document.querySelector("[data-formula]");
  if (formula && window.matchMedia("(max-width: 560px)").matches) formula.open = false;

  // --- calculator ---------------------------------------------------------------------------
  const form = document.querySelector("[data-calculator]");
  const calc = window.ZambetCalculator;
  if (!form || !calc) return;

  const inputs = {};
  const ranges = {};
  form.querySelectorAll("[data-calc-input]").forEach((el) => { inputs[el.dataset.calcInput] = el; });
  form.querySelectorAll("[data-calc-range]").forEach((el) => { ranges[el.dataset.calcRange] = el; });
  const outputs = document.querySelectorAll("[data-calc-out]");

  const render = () => {
    const result = calc.compute({
      inquiries: inputs.inquiries.value,
      missedPct: inputs.missedPct.value,
      conversionPct: inputs.conversionPct.value,
      valueRon: inputs.valueRon.value,
    });
    outputs.forEach((el) => {
      const text = result.formatted[el.dataset.calcOut];
      if (el.textContent !== text) el.textContent = text;
    });
  };

  Object.keys(inputs).forEach((key) => {
    const number = inputs[key];
    const range = ranges[key];
    number.addEventListener("input", () => {
      const value = calc.num(number.value, Number(number.max));
      range.value = String(Math.min(value, Number(range.max)));
      render();
    });
    range.addEventListener("input", () => {
      number.value = range.value;
      render();
    });
  });
  form.addEventListener("submit", (event) => event.preventDefault());
  render();
})();
