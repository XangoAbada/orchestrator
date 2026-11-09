import { View, div, svg, image } from "gpui-kit";
import { h_flex } from "gpui-base";
import { spaces, panes, run, openOverlay, theme, window, shared, setShared, layout, layouts, settings, terminals, hc, notify } from "herdr";

// Orkiestrator: terminal herdr-core bez etykiet herdr (nie tworzy space'a), pokazany w panelu
// jako kolumna PANE: tablica zadań (mod tasks) nad terminalem.
// Herdr usuwa blok {terminal} z layoutu, gdy terminal się kończy, i spłaszcza jednodzietne kolumny,
// więc openOrch odtwarza całość.
const ORCH = "orchestrator";
const PANE = "orch-col";
const ORCH_DIR = "D:\\Projects\\orchestrator";
const orchColumn = () => ({ id: PANE, type: "column", divider: { width: 1, color: "border" }, children: [
  { id: "tasks", type: "block", content: { mod: "tasks" }, size: { weight: 2 }, min: 120 },
  { id: ORCH, type: "block", content: { terminal: ORCH }, size: { weight: 3 }, min: 160 },
] });
const orchTerminal = () => terminals().find(x => x.name === ORCH && x.state === "live");
// Widok pełnookienny: layouts/orch.json (pasek, czat orkiestratora | zadania).
const FULL = "orch";
const fullBody = () => ({ id: "orch-body", type: "row", padding: 8, gap: 8, children: [
  { id: ORCH, type: "block", size: { weight: 1 }, min: 320, radius: 10, background: "background",
    border: { width: 1, color: "border" }, focus: { border: { width: 1, color: "accent" } }, content: { terminal: ORCH } },
  { id: "tasks", type: "block", size: { weight: 1 }, min: 280, radius: 10, border: { width: 1, color: "border" }, content: { mod: "tasks" } },
] });

async function ensureOrch() {
  const live = orchTerminal();
  if (live) return live;
  const r = await hc(["run", "--bg", "--keep", "--json", "--name", ORCH, "--cwd", ORCH_DIR, "--", "powershell.exe", "-NoExit", "-Command", "& 'claude'"]);
  if (r.code) { notify({ title: "Orkiestrator", body: r.stderr || r.stdout, level: "error" }); return null; }
  return JSON.parse(r.stdout);
}

async function toggleFull() {
  if (shared("layout") === FULL) {
    const prev = shared("orch.prev");
    return run("layout.switch", { name: layouts().includes(prev) && prev !== FULL ? prev : "main" });
  }
  if (!(await ensureOrch())) return;
  setShared("orch.prev", shared("layout"));
  run("layout.switch", { name: FULL });
}

async function openOrch() {
  const live = await ensureOrch();
  if (!live) return;
  const tree = JSON.parse(JSON.stringify(layout()));
  const body = find(tree, "panel-body");
  let changed = false;
  if (!find(tree, "panel")) {
    // usuwając blok, herdr spłaszcza jednodzietną kolumnę "panel" (rozmiar, ukrywanie); odbuduj ją
    const parent = findParent(tree, "panel-body");
    parent.children[parent.children.indexOf(body)] = { id: "panel", type: "column", size: { px: 368 }, min: 280, max: 900, children: [body] };
    changed = true;
  }
  if (!find(tree, PANE) || !find(tree, ORCH) || !find(tree, "tasks")) {
    body.children = body.children.filter(c => ![PANE, ORCH, "tasks"].includes(c.id));
    body.children.push(orchColumn());
    changed = true;
  }
  if (changed) run("layout.set", { layout: tree });
  run("layout.select", { stack: "panel-body", child: PANE });
  run("layout.show", { id: "panel" });
  run("pane.focus", { terminal: ORCH });
}

function findParent(node, id) {
  for (const child of node?.children ?? []) {
    if (child.id === id) return node;
    const found = findParent(child, id);
    if (found) return found;
  }
  return null;
}

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

export default class Tabs extends View {
  init() {
    this.pressed = null;
    this.panels = JSON.parse(localStorage.getItem("panels") ?? "{}");
  }

  // The side panel is remembered per space: switching shows that space's choice
  // (closed if it never had one), and opening or closing it records it.
  syncPanel(space, panel, body, enabled) {
    if (!space || !panel) return;
    const now = panel.hidden ? null : body?.active ?? null;
    // Orkiestrator jest globalny: zostaje otwarty przy zmianie space'a i nie nadpisuje jego zapamiętanego panelu.
    if (now === PANE) { this.space = space.id; this.pending = null; return; }
    if (space.id !== this.space) {
      const first = this.space === undefined;
      this.space = space.id; this.pending = null;
      if (!(first && !(space.id in this.panels))) {
        const stored = this.panels[space.id] ?? null, want = enabled.includes(stored) ? stored : null;
        if (want !== now) {
          this.pending = { want, until: Date.now() + 1000 };
          if (want) { run("layout.select", { stack: "panel-body", child: want }); run("layout.show", { id: "panel" }); }
          else run("layout.hide", { id: "panel" });
          return;
        }
      }
    }
    // Until the restore lands (or something else won), the old state isn't a choice.
    if (this.pending) {
      if (this.pending.want !== now && Date.now() < this.pending.until) return;
      this.pending = null;
    }
    if (this.panels[space.id] === now) return;
    this.panels[space.id] = now;
    localStorage.setItem("panels", JSON.stringify(this.panels));
  }

  render(cx) {
    const t = theme();
    const space = spaces().find(p => p.active);
    const shown = space?.tabs ?? [];
    const stop = (_e, cx) => cx.stop_propagation();
    const button = (id, label, action) => div().id(id).size(26).flex_none().flex().items_center().justify_center().rounded(6).text_color(t.dim).hover(s => s.bg(t.hover).text_color(t.foreground))
      .on_mouse_down("left", stop).on_click(action).child(label);
    const item = (c, i) => {
      const program = panes().find(p => p.tab === c.id && p.program?.agent)?.program ?? panes().find(p => p.tab === c.id)?.program;
      const name = /^tb\d+$/.test(c.name) ? program?.name ?? "Terminal" : c.name;
      return h_flex().id(`tab-${c.id}`).h(29).px(10).gap(7).rounded(7).items_center().flex_none().text_size(12.8)
        .border(1).border_color(c.active ? t.border : t.chrome).bg(c.active ? t.raised : t.chrome).text_color(c.active ? t.foreground : t.muted)
        .hover(s => s.bg(t.hover))
        .on_mouse_down("left", (e, cx) => { cx.stop_propagation(); this.pressed = i; if (e.click_count === 2) run("tab.rename", { id: c.id }); })
        .on_mouse_up("left", () => {
          const from = this.pressed; this.pressed = null;
          if (from !== null && from !== i) run("tab.move", { id: shown[from].id, index: space.tabs.findIndex(x => x.id === c.id) + 1 });
          else run("tab.select", { id: c.id });
        })
        .on_mouse_down("right", (_e, cx) => { cx.stop_propagation(); openOverlay({ mod: "menu", kind: "popup", width: 200, props: { tab: "tab", target: { id: c.id, space: space.id } } }); })
        .when(Boolean(program?.icon), el => el.child(program.tint ? svg(program.icon).size(14).text_color(t.muted) : image(program.icon).size(14)))
        .child(div().max_w(170).truncate().child(c.zoomed ? `${name} Z` : name))
        .when(c.status === "working", el => el.child(svg("mods/workspace-chrome/spinner.svg").size(12).flex_none().text_color(t.working).spin(900)))
        .when(c.status === "blocked" || c.status === "done", el => el.child(div().size(6).rounded(3).bg(t[c.status])))
        .when(c.active, el => el.child(div().id(`close-${c.id}`).text_color(t.dim).on_mouse_down("left", stop).on_click((_e, cx) => { cx.stop_propagation(); run("tab.close", { id: c.id }); }).child(svg("mods/pane/close.svg").size(12))));
    };
    const root = layout(), sidebar = find(root, "sidebar"), w = window();
    // Old saved rails and a dragged-narrow sidebar can be smaller than the window buttons.
    const lightsPadding = w?.platform === "macos" && w.titlebar === "none" && !w.fullscreen && root?.traffic_lights
      ? Math.max(10, (root.traffic_lights[0] + 64) / settings().zoom - (sidebar?.hidden ? 0 : sidebar?.size?.px ?? 0))
      : 10;
    const full = shared("layout") === FULL;
    const fullButton = div().id("orch-full").size(26).flex_none().flex().items_center().justify_center().rounded(6)
      .bg(full ? t.selected : t.chrome).text_color(full ? t.foreground : t.dim).hover(s => s.bg(t.hover).text_color(t.foreground))
      .tooltip(full ? "Wróć do poprzedniego układu" : "Orkiestrator na całe okno")
      .on_mouse_down("left", stop).on_click((_e, cx) => cx.spawn(async () => { await toggleFull(); }))
      .child(svg("mods/tabs-orch/fullscreen.svg").size(14));
    const windowButtons = el => el.when(w?.buttons, el => el.child(button("minimize", w.platform === "windows" ? svg("mods/tabs-orch/win-min.svg").size(16) : "−", () => run("window.minimize")))
      .child(button("maximize", w.platform === "windows" ? svg(`mods/tabs-orch/${w.maximized ? "win-restore" : "win-max"}.svg`).size(16) : "□", () => run("window.maximize")))
      .child(button("close-window", w.platform === "windows" ? svg("mods/tabs-orch/win-close.svg").size(16) : svg("mods/pane/close.svg").size(13), () => run("window.close"))));
    if (full) {
      const live = orchTerminal();
      // Herdr usuwa blok terminala, gdy ten się kończy: odbuduj ciało widoku (najwyżej raz na 5 s, bez pętli).
      if (!find(root, ORCH) && !(Date.now() - (this.healed ?? 0) < 5000)) {
        this.healed = Date.now();
        cx.spawn(async () => { if (await ensureOrch()) run("layout.set", { layout: { ...root, children: [find(root, "orch-bar"), fullBody()] } }); });
      } else if (live && this.focused !== live.address && find(root, ORCH)) {
        this.focused = live.address;
        // Blok layoutu odpowiada na nazwę, którą ma w "content.terminal", nie na adres t28.
        cx.timer.after(300, () => run("pane.focus", { terminal: ORCH }));
      }
      return windowButtons(h_flex().size_full().min_w(0).px(10).pl(lightsPadding).gap(6).items_center().bg(t.chrome).border_b(1).border_color(t.border)
        .child(svg("mods/tabs-orch/orchestrator.svg").size(14).text_color(t.muted))
        .child(div().text_size(12.8).text_color(t.foreground).child("Orkiestrator"))
        .child(div().text_size(12).text_color(t.dim).child(live ? live.address : "uruchamianie…"))
        .child(div().flex_1())
        .child(fullButton));
    }
    const panel = find(root, "panel"), body = find(root, "panel-body");
    const enabled = shared("workspace.panels") ?? ["files", "changes"];
    this.syncPanel(space, panel, body, enabled);
    const panelButton = (id, label, icon) => {
      const on = panel && !panel.hidden && body?.active === id;
      return h_flex().id(`panel-${id}`).h(26).px(8).gap(5).flex_none().items_center().rounded(6)
        .text_size(12).bg(on ? t.selected : t.chrome).text_color(on ? t.foreground : t.muted)
        .hover(s => s.bg(t.hover)).on_mouse_down("left", stop)
        .on_click(() => {
          if (on) run("layout.hide", { id: "panel" });
          else { run("layout.select", { stack: "panel-body", child: id }); run("layout.show", { id: "panel" }); }
        }).child(svg(icon).size(14)).child(label);
    };
    const orchOn = panel && !panel.hidden && body?.active === PANE;
    const orchButton = h_flex().id("panel-orchestrator").h(26).px(8).gap(5).flex_none().items_center().rounded(6)
      .text_size(12).bg(orchOn ? t.selected : t.chrome).text_color(orchOn ? t.foreground : t.muted)
      .hover(s => s.bg(t.hover)).on_mouse_down("left", stop)
      .on_click((_e, cx) => {
        if (orchOn) return run("layout.hide", { id: "panel" });
        cx.spawn(async () => { await openOrch(); });
      }).child(svg("mods/tabs-orch/orchestrator.svg").size(14)).child("Orkiestrator");
    const bar = h_flex().size_full().min_w(0).px(10).pl(lightsPadding).gap(3).items_center().bg(t.chrome).border_b(1).border_color(t.border)
      .on_mouse_up("left", () => { this.pressed = null; })
      .on_scroll_wheel(e => { if (Math.abs(e.delta.y) >= 1) run(e.delta.y > 0 ? "tab.prev" : "tab.next"); })
      .child(h_flex().id("tab-scroll").flex_1().min_w(0).h_full().items_center().gap(3).overflow_x_scrollbar().children(shown.map(item)))
      .child(button("tab-new", "+", () => openOverlay({ mod: "new-menu", kind: "popup", width: 250, height: 512 })))
      .when(enabled.includes("files"), el => el.child(panelButton("files", "Files", "mods/files/file.svg")))
      .when(enabled.includes("changes"), el => el.child(panelButton("changes", "Changes", "mods/changes/changes.svg")))
      .child(orchButton)
      .child(fullButton);
    return windowButtons(bar);
  }
}
