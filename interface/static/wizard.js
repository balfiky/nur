import { createSurfaceState } from "/assets/lib/shared.js";

export const WizardState = createSurfaceState({
  open: false,
  step: 1,
  preset: null,
}, {
  open(value) {
    const overlay = document.getElementById("wizardOverlay");
    if (!overlay) return;
    overlay.classList.toggle("open", !!value);
    overlay.setAttribute("aria-hidden", value ? "false" : "true");
  },
  step(value) {
    for (let i = 1; i <= 6; i++) {
      const panel = document.getElementById("wizPanel-" + i);
      if (panel) panel.hidden = i !== value;
      const stepEl = document.querySelector('.wizard-step[data-step="' + i + '"]');
      if (stepEl) {
        stepEl.classList.toggle("active", i === value);
        stepEl.classList.toggle("done", i < value);
      }
    }
  },
  preset(value) {
    document.querySelectorAll(".wizard-preset[data-preset]").forEach((el) => {
      el.classList.toggle("selected", el.dataset.preset === value);
    });
  },
});
