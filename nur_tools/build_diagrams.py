"""Diagram generators for Project Nūr.

Each ``build_*()`` function returns a Canvas that composes the SVG for one
diagram.  ``tools/render_diagram_pngs.py`` drives this module, writing SVG
sources and rasterized PNGs into ``docs/diagrams/``.

The six diagrams were redesigned from scratch for clarity:

- ``runtime_architecture``: four-column flow (clients → hosts → cognition
  core → external/storage), with the cognitive core emphasized and the
  tool-execution path shown as an optional side branch.
- ``single_turn_cognitive_flow``: one vertical spine with deterministic
  stages on the left, gated/LLM paths branching right; stage labels carry
  LLM-call counts.
- ``memory_persistence_model``: filesystem tree on the left, deletion
  boundary on the right.  Shared vs per-user split is visually obvious.
- ``auth_tool_safety_boundary``: two side-by-side gate columns (HTTP
  auth, side-effect gate).  No crossing arrows.
- ``evaluation_ablation_harness``: left-to-right pipeline (inputs →
  per-variant execution → provenance → artifacts).
- ``component_claim_map``: 2×3 tile grid, one component per tile, with a
  status badge and one-line justification.

Regenerate with ``python3 tools/render_diagram_pngs.py``.
"""

from __future__ import annotations

from nur_tools.diagram_toolkit import Canvas, THEME

INK_SOFT = THEME["ink_soft"]
THEME_INK_SOFT = INK_SOFT  # alias used below


# ---------------------------------------------------------------------------
# 1. Runtime architecture
# ---------------------------------------------------------------------------


def build_runtime_architecture() -> Canvas:
    c = Canvas(
        width=1600, height=900,
        title="Project Nūr — Runtime Architecture",
        subtitle=(
            "Two runtime hosts share one session and cognition core. "
            "Persistence, LLM, and tool execution live at the edges."
        ),
    )

    # ---- Column group frames ------------------------------------------
    c.group(40, 120, 240, 700, "Clients", color="client")
    c.group(300, 120, 250, 700, "Runtime hosts", color="process")
    c.group(570, 120, 380, 700, "Shared session + cognition",
            color="attention")
    c.group(970, 120, 590, 700, "External & persistence", color="storage")

    # ---- Clients -------------------------------------------------------
    # Ordered so each client's arrow to its host does not cross the other
    # host: Terminal + Telegram talk to nur (top); Browser + API client
    # talk to nur-web (bottom).
    c.tile(60, 165, 200, 60, "Terminal",
           body=["stdin / stdout"], color="client")
    c.tile(60, 245, 200, 60, "Telegram user",
           body=["optional channel"], color="client")
    c.tile(60, 370, 200, 60, "Browser",
           body=["chat UI, /admin, /docs"], color="client")
    c.tile(60, 450, 200, 60, "API client",
           body=["curl, NurClient, dashboards"], color="client")

    # ---- Hosts ---------------------------------------------------------
    c.tile(320, 220, 210, 90, "nur",
           body=["console runtime,", "Telegram, debug API"],
           color="process", title_size=17)
    c.tile(320, 420, 210, 90, "nur-web",
           body=["FastAPI: chat UI, /admin,", "/v1, legacy endpoints"],
           color="process", title_size=17)

    # ---- Cognitive core -----------------------------------------------
    c.tile(600, 175, 320, 70, "SessionManager",
           body=["per-user locks, backpressure, eviction"],
           color="attention", title_size=15)
    c.tile(600, 275, 320, 70, "UserSession",
           body=["serialized turns per platform:user:chat"],
           color="attention", title_size=15)
    c.tile(600, 375, 320, 90, "CognitivePipeline",
           body=["synchronous stateful turn processor",
                 "(see Single-Turn Cognitive Flow)"],
           color="attention", title_size=16)
    c.tile(600, 505, 320, 70, "Tool executor",
           body=["only active when tools_enabled: true"],
           color="danger", title_size=15)

    # ---- External & persistence ---------------------------------------
    # Single column of outbound resources so arrows can fan without crossing.
    c.tile(1000, 165, 540, 70, "LLM backend",
           body=["mock / provider / openai_compatible / codex / minimax"],
           color="external", title_size=15)
    c.cylinder(1000, 265, 540, 90, "Per-user nur.db",
               body=["memory, relationships, profiles, semantic"],
               color="storage")
    c.cylinder(1000, 390, 540, 80, "Session JSON",
               body=["engine snapshot per platform:user:chat"],
               color="storage")
    c.cylinder(1000, 500, 540, 80, "Shared self_model.db",
               body=["assistant self observations, defense events"],
               color="storage")
    c.tile(1000, 615, 540, 70, "Filesystem workspace",
           body=["sandbox root, default <data_dir>/workspace"],
           color="danger", title_size=15)

    # ---- Arrows: clients → hosts --------------------------------------
    # nur handles stdin and Telegram; nur-web handles HTTP and the API.
    c.arrow((260, 195), (320, 255), label="stdin")
    c.arrow((260, 275), (320, 275), label="bot")
    c.arrow((260, 400), (320, 450), label="HTTP/WS")
    c.arrow((260, 480), (320, 470), label="HTTP API")

    # ---- Hosts → SessionManager ---------------------------------------
    c.arrow((530, 265), (600, 205))
    c.arrow((530, 465), (600, 215))

    # ---- Inside core: vertical spine ----------------------------------
    c.arrow((760, 245), (760, 275))
    c.arrow((760, 345), (760, 375))
    c.arrow((760, 465), (760, 505), dashed=True, label="optional")

    # ---- Core → external (diagonal fan, monotonic y ordering) ---------
    # Source y's and target y's both increase top-to-bottom so the lines
    # fan out without crossing each other.
    c.arrow((920, 390), (1000, 200), label="generate")
    c.arrow((920, 410), (1000, 305), label="read/write")
    c.arrow((920, 430), (1000, 430), label="save")
    c.arrow((920, 450), (1000, 540), label="self-model")
    c.arrow((920, 540), (1000, 650), label="sandboxed",
            dashed=True, color="#cf222e")

    # ---- Footer legend + note -----------------------------------------
    c.legend(
        60, 760,
        [
            ("client", "client channel"),
            ("process", "runtime host"),
            ("attention", "session + cognition"),
            ("storage", "persistent store"),
            ("external", "external LLM"),
            ("danger", "side-effect gate"),
        ],
        title="Legend",
    )
    c.note(
        720, 860,
        "Config read at startup by both hosts: runtime_config.yaml (cwd) · "
        "config/soul.yaml or $NUR_CONFIG_DIR/soul.yaml",
        size=12, fill=THEME_INK_SOFT,
    )
    return c


# ---------------------------------------------------------------------------
# 2. Single-turn cognitive flow
# ---------------------------------------------------------------------------


def build_single_turn_flow() -> Canvas:
    c = Canvas(
        width=1600, height=900,
        title="Single-Turn Cognitive Flow",
        subtitle=(
            "pipeline.process in 5 sections, left to right. "
            "Typical LLM budget: 1 call. Max with all gates firing: ~8."
        ),
    )

    # ---- Top progression bar ------------------------------------------
    sections = [
        ("State update",   "always · 0 LLM",        "process"),
        ("Deliberation",   "gated · 0–5 LLM",       "attention"),
        ("Generation",     "always · +1 LLM",       "external"),
        ("Self-check",     "always + gated · 0–2",  "process"),
        ("Post-processing", "always · 0 LLM",       "neutral"),
    ]
    col_x  = [40, 350, 660, 900, 1250]
    col_w  = [290, 290, 220, 330, 310]

    # Stage headers (progress bar)
    for i, (title, cost, color) in enumerate(sections):
        c.pill(col_x[i], 120, col_w[i], 42, title, color=color)
        c.note(col_x[i] + col_w[i] / 2, 180, cost, size=12,
               fill=INK_SOFT, anchor="middle")

    # Horizontal arrows between section headers
    for i in range(len(sections) - 1):
        a = col_x[i] + col_w[i]
        b = col_x[i + 1]
        c.arrow((a + 2, 141), (b - 2, 141))

    # ---- Section 1: State update (always, 0 LLM) ----------------------
    c.group(col_x[0], 210, col_w[0], 640, "Deterministic pre-pass",
            color="process")
    stages = [
        ("Prepare",
         ["elapsed decay", "anticipation (pre-shift)"]),
        ("Interpret",
         ["contagion detect", "social appraisal",
          "person baseline shift"]),
        ("Update state",
         ["event classify + PSI",
          "resolution update",
          "short-term memory write",
          "spike-write → LTM"]),
        ("Retrieve context",
         ["long-term retrieval",
          "profiles (person / self / topic)",
          "relationship + open loops",
          "contradiction check"]),
    ]
    y = 250
    for title, items in stages:
        h = 30 + 17 * len(items)
        c.tile(col_x[0] + 15, y, col_w[0] - 30, h, title,
               body=items, color="process", title_size=14, body_size=11)
        y += h + 12

    # ---- Section 2: Deliberation (gated) ------------------------------
    c.group(col_x[1], 210, col_w[1], 640, "Gated deliberation",
            color="attention")
    del_stages = [
        ("Inner dialogue",
         ["fast/slow negotiation",
          "2–5 LLM calls depending on rounds",
          "bypassed when arousal > 0.8"],
         "external"),
        ("Tool loop",
         ["intent regex → execute → observe",
          "only when tools_enabled: true",
          "0–N LLM calls"],
         "danger"),
        ("Defense shaping",
         ["rationalize / deflect / minimize / project",
          "injects prompt instruction",
          "0 LLM"],
         "process"),
    ]
    y = 250
    for title, items, color in del_stages:
        h = 30 + 17 * len(items)
        c.tile(col_x[1] + 15, y, col_w[1] - 30, h, title,
               body=items, color=color, title_size=14, body_size=11)
        y += h + 16

    # ---- Section 3: Generation (always, +1 LLM) -----------------------
    c.group(col_x[2], 210, col_w[2], 640, "Generation", color="external")
    c.tile(col_x[2] + 15, 310, col_w[2] - 30, 160,
           "Master LLM generation",
           body=["builds prompt from current",
                 "state, memory, profiles,",
                 "relationship context,",
                 "and deliberation candidate",
                 "",
                 "+1 LLM call"],
           color="external", title_size=15, body_size=12)

    # ---- Section 4: Self-check (always + gated) -----------------------
    c.group(col_x[3], 210, col_w[3], 640, "Self-check", color="process")
    sc_stages = [
        ("Rule self-check",
         ["tone fit / overconfidence /",
          "bluntness / energy fit",
          "always runs · 0 LLM"],
         "process"),
        ("LLM self-check",
         ["gated on rule issues",
          "optional +1 LLM"],
         "external"),
        ("Regenerate",
         ["gated on self-check failure",
          "fail-closed, +1 LLM"],
         "external"),
    ]
    y = 250
    for title, items, color in sc_stages:
        h = 30 + 17 * len(items)
        c.tile(col_x[3] + 15, y, col_w[3] - 30, h, title,
               body=items, color=color, title_size=14, body_size=11)
        y += h + 16

    # ---- Section 5: Post-processing (always, 0 LLM) -------------------
    c.group(col_x[4], 210, col_w[4], 640, "Post-processing",
            color="neutral")
    post_stages = [
        ("Update memory + state",
         ["short-term → long-term digest",
          "relationship open-loop updates",
          "trust / bonding / maturity deltas"]),
        ("Drain energy",
         ["per-message and spike drain"]),
        ("Capture debug trace",
         ["DebugState assembled",
          "(modulators, dialogue, defense,",
          " unresolved items, memories)"]),
        ("Return",
         ["Response + DebugState",
          "back to channel / caller"]),
    ]
    y = 250
    for title, items in post_stages:
        h = 30 + 17 * len(items)
        c.tile(col_x[4] + 15, y, col_w[4] - 30, h, title,
               body=items, color="neutral", title_size=14, body_size=11)
        y += h + 14

    # ---- Footer legend / note -----------------------------------------
    c.legend(
        60, 872,
        [
            ("process", "deterministic, 0 LLM"),
            ("attention", "gated (condition-dependent)"),
            ("external", "LLM call (+N)"),
            ("danger", "side-effect path, gated by tools_enabled"),
        ],
    )
    return c


# ---------------------------------------------------------------------------
# 3. Memory and persistence
# ---------------------------------------------------------------------------


def build_memory_persistence() -> Canvas:
    c = Canvas(
        width=1600, height=900,
        title="Memory and Persistence",
        subtitle=(
            "Per-user data is isolated and deletable. "
            "The shared assistant self-model is preserved by design."
        ),
    )

    # Layout: filesystem tree on the left (wide), deletion boundary right.
    c.group(40, 120, 1040, 680, "data/   (configured via data_dir)",
            color="storage")
    c.group(1100, 120, 460, 680, "Deletion boundary", color="danger")

    # ---- Shared DB ----------------------------------------------------
    c.cylinder(90, 170, 380, 140, "data/shared/self_model.db",
               body=["observations (entity_id = '__self__')",
                     "extracted_traits", "defense_events"],
               color="storage")
    c.note(290, 330, "preserved across user deletions",
           size=12, fill="#1a7f37", anchor="middle", weight=700)

    # ---- Per-user dir -------------------------------------------------
    c.group(80, 360, 980, 420,
            "data/<platform>_<user_id>/   (removed on user deletion)",
            color="process")

    c.cylinder(120, 410, 420, 200, "nur.db   (per-user SQLite)",
               body=["memories",
                     "relationship_events",
                     "open_loops",
                     "observations   (person + topic)",
                     "extracted_traits",
                     "semantic_memories"],
               color="storage")

    c.tile(580, 410, 440, 80, "sessions/<chat_id>.json",
           body=["engine_state snapshot per chat"],
           color="blank", title_size=14)
    c.tile(580, 510, 440, 60, "engine_state.json",
           body=["legacy per-user snapshot (backward-compat)"],
           color="blank", title_size=14)

    c.tile(120, 640, 900, 60, "Key derivation",
           body=["rel_key = platform:user_id   ·   "
                 "session_key = platform:user_id:chat_id"],
           color="neutral", title_size=14)
    c.tile(120, 710, 900, 50, "Config (read at startup; writes via /admin)",
           body=["runtime_config.yaml in cwd   ·   "
                 "config/soul.yaml or $NUR_CONFIG_DIR/soul.yaml"],
           color="neutral", title_size=14)

    # ---- Deletion boundary panel --------------------------------------
    c.tile(1130, 170, 400, 70, "DELETE /v1/users/{platform}/{user_id}",
           body=["wipes per-user dir, evicts sessions"],
           color="danger", title_size=13)
    c.tile(1130, 260, 400, 70, "Admin: typed DELETE confirmation",
           body=["DELETE <platform>:<user_id>"],
           color="danger", title_size=13)

    c.tile(1130, 370, 400, 110, "Removed",
           body=["per-user nur.db",
                 "sessions/ and engine_state.json",
                 "the per-user directory itself"],
           color="process", title_size=14)

    c.tile(1130, 510, 400, 110, "Preserved",
           body=["data/shared/self_model.db",
                 "(entity_id = '__self__' only)",
                 "intentional: assistant keeps its own history"],
           color="storage", title_size=14)

    c.tile(1130, 650, 400, 120, "Row-count reporting",
           body=["DELETE endpoint returns best-effort",
                 "row counts per table; missing counts",
                 "report as null but the wipe still",
                 "proceeds."],
           color="neutral", title_size=14)

    # ---- Arrows -------------------------------------------------------
    # Keep arrows short and orthogonal so they don't cross file-tree tiles.
    # "wipes" points from the Removed card to the per-user group frame.
    c.connector(
        [(1130, 425), (1090, 425), (1090, 570), (1060, 570)],
        label="wipes", dashed=True, color="#cf222e",
    )
    # "untouched" points from the Preserved card to the shared self-model DB.
    c.connector(
        [(1130, 565), (1090, 565), (1090, 240), (470, 240)],
        label="untouched", dashed=True, color="#1a7f37",
    )

    return c


# ---------------------------------------------------------------------------
# 4. Auth and tool-safety boundary
# ---------------------------------------------------------------------------


def build_auth_tool_safety() -> Canvas:
    c = Canvas(
        width=1600, height=920,
        title="Auth and Tool-Safety Boundaries",
        subtitle=(
            "Two independent switches. api_key controls HTTP access; "
            "tools_enabled + shell_tool_enabled control side effects. "
            "Turning one on does not turn on the other."
        ),
    )

    c.group(40, 120, 720, 700, "HTTP request gate — api_key",
            color="process")
    c.group(800, 120, 760, 700, "Side-effect gate — tools_enabled + shell_tool_enabled",
            color="danger")

    # ---- HTTP gate ----------------------------------------------------
    c.tile(80, 170, 640, 60, "Incoming HTTP / WebSocket request",
           body=["/chat, /debug, /config, /admin/*, /v1/*, /ws"],
           color="process", title_size=15)

    c.diamond(400, 300, 220, 80, "api_key configured?", color="attention")

    c.tile(80, 440, 300, 80, "Open",
           body=["every route bootstraps", "no Authorization header needed"],
           color="blank", title_size=14)
    c.tile(420, 440, 300, 80, "Require Bearer token",
           body=["Authorization: Bearer <api_key>",
                 "re-read on every request"],
           color="process", title_size=14)

    c.diamond(570, 590, 220, 80, "Token valid?", color="attention")
    c.tile(420, 700, 140, 60, "200 OK", color="client", title_size=14)
    c.tile(580, 700, 140, 60, "401", color="danger", title_size=14)

    c.tile(80, 580, 300, 60, "Always open",
           body=["GET /, /admin HTML, /v1/health, /v1/ready"],
           color="client", title_size=13)

    # ---- Tool gate ----------------------------------------------------
    c.tile(830, 170, 710, 60, "/chat turn reaches CognitivePipeline",
           body=["tool_loop regex intent match"],
           color="neutral", title_size=15)

    c.diamond(1180, 300, 220, 80, "tools_enabled?", color="attention")

    c.tile(830, 440, 300, 80, "No tool execution",
           body=["text-only response",
                 "safe default"],
           color="process", title_size=14)
    c.tile(1250, 440, 300, 80, "Filesystem tools",
           body=["paths resolve inside tools_workspace",
                 "out-of-sandbox paths refused"],
           color="danger", title_size=14)

    c.diamond(1400, 590, 220, 80, "shell_tool_enabled?", color="attention")
    c.tile(1170, 700, 180, 60, "Shell off",
           body=["default"],
           color="process", title_size=13)
    c.tile(1380, 700, 180, 60, "shell.run_command",
           body=["explicit opt-in, trusted callers only"],
           color="danger", title_size=13)

    # ---- Arrows (HTTP) ------------------------------------------------
    c.arrow((400, 230), (400, 260))
    c.arrow((310, 340), (230, 440), label="no", route="direct")
    c.arrow((490, 340), (570, 440), label="yes", route="direct")
    c.arrow((570, 520), (570, 590))
    c.arrow((510, 625), (490, 700), label="yes", route="direct")
    c.arrow((640, 625), (660, 700), label="no", route="direct")

    # Always-open → OK (implicit; draw a thin dashed explanatory arrow)
    c.arrow((380, 610), (420, 730), label="bypass", dashed=True,
            color="#1a7f37", route="direct")

    # ---- Arrows (tool) ------------------------------------------------
    c.arrow((1180, 230), (1180, 260))
    c.arrow((1100, 340), (980, 440), label="false", route="direct")
    c.arrow((1260, 340), (1400, 440), label="true", route="direct")
    c.arrow((1400, 520), (1400, 590))
    c.arrow((1335, 625), (1260, 700), label="false", route="direct")
    c.arrow((1465, 625), (1470, 700), label="true", route="direct")

    # ---- Footer legend / defaults -------------------------------------
    c.tile(60, 830, 1480, 56, "Tracked defaults",
           body=[
               "api_key='', cors_origins=[], tools_enabled=false, "
               "tools_workspace='', shell_tool_enabled=false — "
               "localhost-first starter posture, not a production posture."
           ],
           color="neutral", title_size=14, body_size=12)

    return c


# ---------------------------------------------------------------------------
# 5. Evaluation / ablation harness
# ---------------------------------------------------------------------------


def build_evaluation_harness() -> Canvas:
    c = Canvas(
        width=1600, height=820,
        title="Evaluation and Ablation Harness",
        subtitle=(
            "Scripted scenarios run against the real pipeline. "
            "One component disabled per variant. Every run is provenance-stamped."
        ),
    )

    c.group(40, 120, 300, 540, "Inputs", color="client")
    c.group(370, 120, 420, 540, "Per-variant execution", color="process")
    c.group(820, 120, 320, 540, "Provenance stamping", color="attention")
    c.group(1170, 120, 390, 540, "Artifacts", color="storage")

    # ---- Inputs -------------------------------------------------------
    c.tile(60, 160, 260, 80, "python3 -m evals",
           body=["--backend {mock|provider|...}", "--tag phase11, --report-dir ..."],
           color="client", title_size=14)
    c.tile(60, 260, 260, 80, "Scenario selector",
           body=["tag phase11 or explicit set"],
           color="client", title_size=14)
    c.tile(60, 360, 260, 80, "PipelineFeatures toggles",
           body=["no_relationship, no_inner_dialogue,",
                 "no_defense, no_semantic"],
           color="attention", title_size=14)
    c.tile(60, 460, 260, 80, "Output flags",
           body=["--report writes per-variant JSON"],
           color="client", title_size=14)

    # ---- Execution ----------------------------------------------------
    c.tile(390, 160, 380, 80, "Fresh CognitivePipeline",
           body=["per scenario — no state leakage"],
           color="process", title_size=14)
    c.tile(390, 260, 380, 80, "InstrumentedBackend",
           body=["counts turns, tokens (null today), calls"],
           color="process", title_size=14)
    c.tile(390, 360, 380, 80, "Run turns",
           body=["pipeline.process(message) per scripted turn"],
           color="process", title_size=14)
    c.tile(390, 460, 380, 80, "Evaluate assertions",
           body=["strategy, modulator delta, memory/open-loop effects"],
           color="process", title_size=14)

    # ---- Provenance ---------------------------------------------------
    c.tile(840, 160, 280, 80, "Code identity",
           body=["git SHA, branch, dirty flag"],
           color="attention", title_size=14)
    c.tile(840, 260, 280, 80, "Backend identity",
           body=["requested_model, resolved_model"],
           color="attention", title_size=14)
    c.tile(840, 360, 280, 80, "Config fingerprints",
           body=["SHA-256 of 16 prompt + config files"],
           color="attention", title_size=14)
    c.tile(840, 460, 280, 80, "Outcome labels",
           body=["expected_failure, unexpected_failure,",
                 "no_effect, newly_passing"],
           color="attention", title_size=14)

    # ---- Artifacts ----------------------------------------------------
    c.tile(1190, 160, 350, 80, "Per-variant JSON report",
           body=["turns, latency, LLM calls, pass/fail"],
           color="storage", title_size=14)
    c.tile(1190, 260, 350, 80, "summary.json",
           body=["aggregated across variants"],
           color="storage", title_size=14)
    c.tile(1190, 360, 350, 80, "Honest nulls",
           body=["tokens, cost, retries = null when not",
                 "extracted by the current client"],
           color="neutral", title_size=14)
    c.tile(1190, 460, 350, 80, "Report is self-describing",
           body=["no external context needed to interpret"],
           color="storage", title_size=14)

    # ---- Arrows -------------------------------------------------------
    c.arrow((320, 200), (390, 200), label="backend")
    c.arrow((320, 300), (390, 300), label="scenarios")
    c.arrow((320, 400), (390, 400), label="features")
    c.arrow((770, 200), (840, 200))
    c.arrow((770, 400), (840, 400))
    c.arrow((770, 500), (840, 500))
    c.arrow((1120, 200), (1190, 200))
    c.arrow((1120, 400), (1190, 400))
    c.arrow((1120, 500), (1190, 500))
    c.arrow((1190, 290), (1400, 270), route="v-then-h", dashed=True,
            label="merged")

    # Footer
    c.tile(60, 700, 1480, 70, "Honest framing",
           body=[
               "The harness tests structural outcomes (strategy, modulator "
               "direction, memory writes), not wording quality. "
               "Architecture ablation is the right question for this suite; "
               "architecture comparison against a prompt-only baseline is not "
               "attempted here."
           ],
           color="neutral", title_size=14, body_size=12)
    return c


# ---------------------------------------------------------------------------
# 6. Component claim map
# ---------------------------------------------------------------------------


def build_component_claim_map() -> Canvas:
    c = Canvas(
        width=1600, height=960,
        title="Component Claim Map",
        subtitle=(
            "Evidence status per architectural component under the current "
            "Phase 11 scenario suite. Zero-effect is not inertness — it is "
            "a bounded falsifiability claim about this suite."
        ),
    )

    # Badge colors map to palette roles.
    BADGES = {
        "load-bearing":     ("client",    "LOAD-BEARING"),
        "not-falsifiable":  ("attention", "NOT FALSIFIABLE HERE"),
        "negative-control": ("neutral",   "NEGATIVE CONTROL"),
        "arch-claim":       ("storage",   "ARCHITECTURAL CLAIM"),
        "unit-covered":     ("process",   "UNIT + INTEGRATION"),
    }

    tiles = [
        (
            "Relationship memory", "load-bearing",
            "Disabling breaks exactly the two scenarios that depend on "
            "cross-turn open-loop state; zero unexpected failures.",
        ),
        (
            "Semantic memory", "negative-control",
            "Phase 11 does not exercise semantic retrieval. "
            "Zero effect is the expected result and is the observed result.",
        ),
        (
            "Inner dialogue", "not-falsifiable",
            "Deliberation rounds do not move the strategy selector. "
            "A different suite is needed to falsify its contribution.",
        ),
        (
            "Defense mechanisms", "not-falsifiable",
            "Shapes generator output, which Phase 11 does not grade. "
            "Requires wording-quality scenarios or human rating.",
        ),
        (
            "Self / other profiling", "arch-claim",
            "One ProfileStore for user, topic, and assistant self "
            "(entity_id='__self__'). Design claim, not a benchmark win.",
        ),
        (
            "Emotional modulators", "unit-covered",
            "Numeric state with direct unit and integration coverage: "
            "decay, spike, energy, contagion, context shift.",
        ),
    ]

    cols = 3
    tile_w, tile_h = 460, 250
    gap_x, gap_y = 30, 40
    origin_x = 60
    origin_y = 140
    for i, (title, badge_key, note) in enumerate(tiles):
        row, col = divmod(i, cols)
        tx = origin_x + col * (tile_w + gap_x)
        ty = origin_y + row * (tile_h + gap_y)
        palette, badge_text = BADGES[badge_key]
        p = {
            "load-bearing":     "client",
            "not-falsifiable":  "attention",
            "negative-control": "neutral",
            "arch-claim":       "storage",
            "unit-covered":     "process",
        }[badge_key]
        # Outer card
        c.tile(tx, ty, tile_w, tile_h, title, body=[], color="blank",
               title_size=18)
        # Badge pill
        c.pill(tx + 20, ty + 60, 260, 30, badge_text, color=p)
        # Body text (wrapped manually into shorter chunks below the badge)
        # Simple wrap at ~60 chars by splitting on spaces.
        words = note.split()
        lines: list[str] = []
        line = ""
        for w in words:
            candidate = (line + " " + w).strip()
            if len(candidate) > 60 and line:
                lines.append(line)
                line = w
            else:
                line = candidate
        if line:
            lines.append(line)
        for j, line in enumerate(lines[:6]):
            c.note(tx + 20, ty + 125 + j * 18, line, size=13,
                   fill="#57606a")

    # Footer legend
    c.legend(
        60, 880,
        [
            ("client",    "load-bearing (falsified by current ablation)"),
            ("attention", "not falsifiable by current suite"),
            ("neutral",   "negative control"),
            ("storage",   "architectural claim"),
            ("process",   "unit + integration coverage"),
        ],
        title="Evidence-status legend",
    )
    return c


# ---------------------------------------------------------------------------
# 7. Hero banner — "state that persists between your turns"
# ---------------------------------------------------------------------------


def build_hero_banner() -> Canvas:
    c = Canvas(
        width=1600, height=520,
        title="State that persists between your turns",
        subtitle=(
            "Every turn reads and writes the same cognitive state. "
            "The assistant's stance toward you accumulates instead of "
            "resetting."
        ),
    )

    # Three turn cards across the top row.
    turns = [
        ("Mon", "You: I shipped the feature!",
         "Nūr: That's great — how did it land?",
         "write memory", "client"),
        ("Wed", "You: Rollback was rough.",
         "Nūr: I remember Monday went well — what flipped?",
         "retrieves Mon · opens loop", "attention"),
        ("Fri", "You: Fixed it, feeling better.",
         "Nūr: Good. Want to unpack what broke?",
         "closes loop · trust += repair", "client"),
    ]
    card_w, card_h = 470, 170
    for i, (day, u, n, tag, color) in enumerate(turns):
        x = 60 + i * (card_w + 30)
        y = 130
        c.tile(x, y, card_w, card_h, day, body=[u, "", n, "", tag],
               color=color, title_size=14, body_size=13)

    # The persistent-state band beneath.
    band_y = 340
    c.group(40, band_y, 1520, 110, "Persistent cognitive state",
            color="process")
    pills = [
        ("arousal", "process"),
        ("valence", "process"),
        ("certainty", "process"),
        ("bonding", "process"),
        ("energy", "process"),
        ("resolution", "process"),
        ("memory", "storage"),
        ("relationship arc", "storage"),
        ("self-model", "storage"),
    ]
    pill_w, pill_h = 160, 34
    gap = 10
    total_w = len(pills) * pill_w + (len(pills) - 1) * gap
    start_x = 40 + (1520 - total_w) / 2
    for i, (label, color) in enumerate(pills):
        c.pill(start_x + i * (pill_w + gap), band_y + 50, pill_w, pill_h,
               label, color=color)

    # Descending arrows from each card into the state band.
    for i in range(3):
        cx = 60 + i * (card_w + 30) + card_w / 2
        c.arrow((cx, 300), (cx, band_y - 2))

    return c


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BUILDERS = {
    "runtime-architecture": build_runtime_architecture,
    "single-turn-cognitive-flow": build_single_turn_flow,
    "memory-persistence-model": build_memory_persistence,
    "auth-tool-safety-boundary": build_auth_tool_safety,
    "evaluation-ablation-harness": build_evaluation_harness,
    "component-claim-map": build_component_claim_map,
    "hero-banner": build_hero_banner,
}
