import { marked } from "https://cdn.jsdelivr.net/npm/marked@12.0.2/+esm";
import DOMPurify from "https://cdn.jsdelivr.net/npm/dompurify@3.1.6/+esm";

marked.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });

export const PURIFY_CONFIG = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["style", "svg", "math", "form", "input", "iframe", "object", "embed"],
  FORBID_ATTR: ["style", "onerror", "onload", "onclick"],
  ALLOW_DATA_ATTR: false,
};

export const renderMarkdown = (raw) =>
  DOMPurify.sanitize(marked.parse(String(raw ?? "")), PURIFY_CONFIG);

export function createSurfaceState(initial, renderers) {
  const hooks = renderers || {};
  return new Proxy({ ...initial }, {
    set(target, prop, value) {
      const previous = target[prop];
      target[prop] = value;
      if (typeof hooks[prop] === "function") hooks[prop](value, previous, target);
      if (typeof hooks["*"] === "function") hooks["*"](prop, value, previous, target);
      return true;
    },
  });
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

export function getApiToken() {
  return sessionStorage.getItem("nur_api_token") || "";
}

export function setApiToken(token) {
  if (token) sessionStorage.setItem("nur_api_token", token);
  else sessionStorage.removeItem("nur_api_token");
}

export function authHeaders(extra) {
  const headers = extra ? { ...extra } : {};
  const token = getApiToken();
  if (token) headers.Authorization = "Bearer " + token;
  return headers;
}

export async function authedFetch(url, opts) {
  const next = { ...(opts || {}) };
  next.headers = authHeaders(next.headers || {});
  return fetch(url, next);
}

export function getOrCreateId(key, store) {
  let value = store.getItem(key);
  if (!value) {
    value = crypto.randomUUID ? crypto.randomUUID() : "id-" + Math.random().toString(36).slice(2);
    store.setItem(key, value);
  }
  return value;
}

export function setRootVar(name, value) {
  document.documentElement.style.setProperty(name, value);
}

export function labelize(value) {
  return String(value || "").replaceAll("_", " ");
}

export function splitLoose(value) {
  return String(value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

export function linesFrom(value) {
  return String(value || "").split("\n").map((item) => item.trim()).filter(Boolean);
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}
