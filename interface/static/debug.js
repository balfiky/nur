import { createSurfaceState } from "/assets/lib/shared.js";

export const DebugState = createSurfaceState({
  open: false,
  payload: null,
}, {
  open(value) {
    const panel = document.getElementById("debugPanel");
    const toggle = document.getElementById("debugToggle");
    if (panel) panel.classList.toggle("open", !!value);
    if (toggle) toggle.classList.toggle("active", !!value);
  },
});
