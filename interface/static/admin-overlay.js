import { createSurfaceState } from "/assets/lib/shared.js";

export const AdminOverlayState = createSurfaceState({
  open: false,
  statusHtml: "Runtime configuration for Nūr.",
  statusError: false,
  statusOk: false,
}, {
  open(value) {
    const overlay = document.getElementById("settingsOverlay");
    if (!overlay) return;
    overlay.classList.toggle("open", !!value);
    overlay.setAttribute("aria-hidden", value ? "false" : "true");
  },
  statusHtml(value, _previous, state) {
    const el = document.getElementById("settingsStatus");
    if (!el) return;
    el.classList.toggle("error", !!state.statusError);
    el.classList.toggle("ok", !!state.statusOk && !state.statusError);
    el.innerHTML = value;
  },
});
