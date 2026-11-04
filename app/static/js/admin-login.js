/* Show / hide the admin password. */
(function () {
  "use strict";
  const toggle = document.querySelector("[data-toggle-password]");
  const input = document.getElementById("adm-pass");
  if (!toggle || !input) return;
  toggle.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(show));
    toggle.setAttribute("aria-label", show ? "Ascunde parola" : "Arată parola");
    input.focus();
  });
})();
