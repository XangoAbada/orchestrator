import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex, TextView } from "gpui-base";
import { exec, hc, run, theme, terminals, agents, spaces, watchFolder, notify } from "herdr";

// Tablica zadań orkiestratora. Dane i zmiany tylko przez orch.py (jedno źródło prawdy).
const DIR = "D:\\Projects\\orchestrator";
const ORCH = [DIR + "\\orch.py"];
const ICONS = "mods/tasks/";
// Pilność do sortowania grup: co wymaga uwagi, idzie wyżej.
const RANK = { blocked: 6, review: 5, working: 4, starting: 4, done: 3, idle: 3, ended: 3, todo: 2, finished: 0 };

export default class Tasks extends View {
  init(_props, cx) {
    this.tasks = [];
    this.error = null;
    this.showDone = false;
    this.busy = {};      // id -> co się dzieje
    this.conflict = {};  // id albo wave:<projekt>:<etap> -> {code, msg, args}: orch odmówił (2: kolizja, 3: zablokowane, 4: done przy niezakomitowanych/niescalonych zmianach)
    this.folded = new Set();
    this.expanded = new Set(); // id zadań z rozwiniętym briefem
    cx.spawn(async (cx) => { await this.load(); cx.notify(); });
    cx.spawn(async (cx) => { for (;;) { await watchFolder(DIR); await this.load(); cx.notify(); } });
    // ponytail: ręczne edycje TASKS.md w repo widać po ≤30 s; watch na każdym repo, jeśli to za wolno
    cx.timer.every(30000, (cx) => cx.spawn(async (cx) => { await this.load(); cx.notify(); }));
  }

  async load() {
    try {
      const r = await exec("python", [...ORCH, "list", "--all", "--json"]);
      if (r.code) this.error = (r.stderr || r.stdout).trim();
      else { this.tasks = JSON.parse(r.stdout); this.error = null; }
    } catch (e) {
      this.error = String(e);
    }
  }

  act(cx, id, args, label) {
    this.busy[id] = label;
    cx.notify();
    cx.spawn(async (cx) => {
      const r = await exec("python", [...ORCH, ...args]).catch((e) => ({ code: 1, stderr: String(e), stdout: "" }));
      delete this.busy[id];
      const lines = r.stderr.trim().split("\n");
      // kod 4: bez dwóch ostatnich linii (pytanie dla orkiestratora i podpowiedź --force), te są tu przyciskami
      if (r.code === 4) this.conflict[id] = { code: 4, msg: lines.slice(0, -2).slice(0, 8).join("\n"), args };
      else if (r.code === 2 || r.code === 3) this.conflict[id] = { code: r.code, msg: lines[0], args };
      else if (r.code) notify({ title: `Zadanie ${id}`, body: (r.stderr || r.stdout).trim(), level: "error" });
      else delete this.conflict[id];
      await this.load();
      cx.notify();
    });
  }

  // Stan agenta zadania w toku, na żywo z herdr-core.
  live(task, terms) {
    if (task.status !== "running" || !task.terminal) return null;
    const term = terms.find((x) => x.address === task.terminal);
    if (!term || term.state !== "live") return "ended";
    return term.status ?? "starting";
  }

  // Znacznik w wąskiej kolumnie po lewej.
  badge(t, task, live) {
    const ring = (color) => div().size(8).mx(3).flex_none().rounded(4).border(1.5).border_color(color);
    const dot = (color) => div().size(7).mx(3).flex_none().rounded(4).bg(color);
    if (task.status === "todo" && task.waiting?.length) return svg(ICONS + "lock.svg").size(11).mx(1.5).flex_none().text_color(t.dim);
    if (task.status === "todo") return ring(t.working);
    if (task.status === "review") return dot(t.accent);
    if (task.status === "done") return svg("mods/workspace-chrome/check.svg").size(12).mx(1).flex_none().text_color(t.dim);
    if (live === "working" || live === "starting") return svg("mods/workspace-chrome/spinner.svg").size(12).mx(1).flex_none().text_color(live === "working" ? t.working : t.dim).spin(900);
    if (live === "blocked") return dot(t.blocked);
    if (live === "done") return dot(t.done);
    if (live === "idle") return ring(t.idle ?? t.dim);
    return dot(t.dim); // ended
  }

  // Plakietka przy akcjach, tylko gdy coś wymaga uwagi.
  pill(t, task, live) {
    const pill = (text, color) => div().flex_none().px(6).h(17).rounded(5).bg(color + "26").text_size(10.5).font_semibold()
      .text_color(color).flex().items_center().child(text);
    if (task.status === "review") return pill("Review", t.accent);
    if (live === "blocked") return pill("Czeka", t.blocked);
    if (live === "ended") return pill("Zakończony", t.dim);
    if (task.status === "running" && task.coordinator?.length > 1) return pill("Koordynator", t.muted);
    return null;
  }

  // Okienko po odmowie orch: start z kodem 2 (kolizja w projekcie) albo 3 (zablokowane zależnościami),
  // done z kodem 4 (niezakomitowane zmiany, przy worktree też niescalone commity).
  conflictBox(t, key) {
    const c = this.conflict[key];
    const button = (label, click) => div().id(`${label}-${key}`).px(6).h(20).rounded(5).border(1).border_color(t.border).text_size(11)
      .flex().items_center().cursor_pointer().hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => { delete this.conflict[key]; click(cx); }).child(label);
    const btn = (label, flag) => button(label, (cx) => this.act(cx, key, [...c.args, flag], c.code === 4 ? "…" : "start…"));
    const solo = !key.includes(":"); // zadanie, nie grupa
    const task = this.tasks.find((x) => x.id === key);
    const alive = c.code === 4 && task?.terminal && terminals().some((x) => x.address === task.terminal && x.state === "live");
    // Commit robi agent w swoim terminalu; po nim ✓ jeszcze raz: orch zamknie okno i usunie scalony worktree.
    const commit = () => hc(["send", task.terminal, (task.worktree
      ? `Zakomituj wszystkie zmiany i scal gałąź ${task.branch} do głównej gałęzi w głównym checkoucie (git worktree list). Worktree i gałęzi nie usuwaj, zrobi to orkiestrator przy done.`
      : "Zakomituj wszystkie zmiany.") +
      ` Potem zgłoś: python '${DIR.replaceAll("\\", "/")}/orch.py' edit ${key} --status review --note 'commit'`]);
    return v_flex().ml(solo ? 35 : 8).mr(4).mb(4).p(6).gap(5).rounded(6).bg(t.blocked + "14").text_size(11.5)
      .child(v_flex().text_color(t.muted).children(c.msg.split("\n").map((l) => div().child(l))))
      .child(h_flex().gap(5)
        .when(Boolean(alive), (el) => el.child(button(task.worktree ? "Commit i scal" : "Commit", (cx) => { commit(); cx.notify(); })))
        .when(c.code === 4, (el) => el.child(btn("Zostaw zmiany", "--force")))
        .when(c.code === 3, (el) => el.child(btn("Uruchom mimo to", "--force")))
        .when(c.code === 2 && solo, (el) => el.child(btn("Osobny worktree", "--worktree")))
        .when(c.code === 2, (el) => el.child(btn("Ten sam checkout", "--here")))
        .child(div().id(`dismiss-${key}`).px(6).h(20).rounded(5).text_size(11).text_color(t.dim).flex().items_center().cursor_pointer()
          .hover((s) => s.bg(t.hover)).on_click((_e, cx) => { delete this.conflict[key]; cx.notify(); }).child("Anuluj")));
  }

  // Etap z kilkoma zadaniami niezależnymi od siebie: ramka, nagłówek i start jednej równoległej sesji (koordynator).
  group(t, project, wave, stage, rows) {
    const key = `wave:${project}:${wave}`;
    const ready = stage.filter((x) => x.ready).map((x) => x.id);
    const coord = stage.find((x) => x.status === "running" && x.coordinator?.length > 1);
    const waiting = [...new Set(stage.flatMap((x) => x.status === "todo" ? x.waiting ?? [] : []))];
    const note = this.busy[key] ?? (coord ? `sesja koordynatora · ${coord.terminal}` :
      ready.length < 2 && waiting.length ? `czeka na: ${waiting.join(", ")}` : null);
    const head = h_flex().h(24).pl(9).pr(4).gap(6).flex_none().items_center()
      .child(svg("mods/workspace-chrome/layers.svg").size(11).flex_none().text_color(ready.length > 1 ? t.working : t.dim))
      .child(div().flex_1().min_w(0).truncate().text_size(10.5).text_color(t.dim).child(`Równolegle · ${stage.length}`))
      .when(Boolean(note), (el) => el.child(div().flex_none().text_size(10.5).text_color(t.dim).child(note)))
      .when(ready.length > 1 && !this.busy[key], (el) => el.child(
        h_flex().id(`run-${key}`).flex_none().gap(4).px(7).h(19).rounded(5).items_center().cursor_pointer()
          .text_size(10.5).font_semibold().text_color(t.accent).bg(t.accent + "1f").hover((s) => s.bg(t.accent + "33"))
          .tooltip(`Jeden agent-koordynator prowadzi równolegle ${ready.join(", ")}, scala je do głównej gałęzi i sprząta worktree`)
          .on_click((_e, cx) => { cx.stop_propagation(); this.act(cx, key, ["start", ...ready], "start…"); })
          .child(svg(ICONS + "play.svg").size(10)).child(`Uruchom razem (${ready.length})`)));
    return v_flex().id(`grp-${key}`).my(3).pb(2).gap(1).flex_none().rounded(7).border(1)
      .border_color(ready.length > 1 ? t.working + "55" : t.border).bg(t.surface)
      .child(head).children(rows).when(Boolean(this.conflict[key]), (el) => el.child(this.conflictBox(t, key)));
  }

  icon(t, id, file, tip, action) {
    return div().id(id).flex_none().p(3).rounded(4).text_color(t.dim).cursor_pointer()
      .hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip(tip)
      .on_click((_e, cx) => { cx.stop_propagation(); action(cx); })
      .child(svg(file).size(13));
  }

  row(t, task, live, agentList) {
    const id = task.id;
    const agent = agentList.find((a) => a.address === task.terminal);
    const go = agent ? () => run("pane.focus", { pane: agent.pane }) : null;
    const ask = () => hc(["send", "orchestrator", `Co z zadaniem ${id}?`]);
    const actions = [];
    if (this.busy[id]) actions.push(div().text_size(11).text_color(t.dim).child(this.busy[id]));
    else {
      if (task.status === "todo") actions.push(this.icon(t, `start-${id}`, ICONS + "play.svg",
        task.waiting?.length ? `Zablokowane (czeka na: ${task.waiting.join(", ")}), uruchomienie z potwierdzeniem` : "Uruchom",
        (cx) => this.act(cx, id, ["start", id], "start…")));
      if (task.status === "review" || live === "ended") actions.push(this.icon(t, `done-${id}`, "mods/workspace-chrome/check.svg", "Oznacz jako zrobione", (cx) => this.act(cx, id, ["edit", id, "--status", "done"], "…")));
      if (go) actions.push(this.icon(t, `go-${id}`, ICONS + "go.svg", `Przejdź do ${task.terminal}`, go));
      actions.push(this.icon(t, `ask-${id}`, ICONS + "message.svg", "Zapytaj orkiestratora", ask));
    }
    const open = this.expanded.has(id);
    const blocked = task.status === "todo" && task.waiting?.length > 0;
    const coord = task.status === "running" && task.coordinator?.length > 1;
    const tip = [task.title,
      `etap ${task.wave}`,
      task.after?.length ? `po: ${task.after.join(", ")}` : null,
      blocked ? `czeka na: ${task.waiting.join(", ")}` : task.ready ? "gotowe do startu" : null,
      coord ? `prowadzi koordynator (${task.terminal}): ${task.coordinator.join(", ")}` : null,
      live && live !== "ended" ? `agent: ${live}` : live === "ended" ? "terminal zakończony" : null]
      .filter(Boolean).join("\n");
    const titleColor = task.status === "done" || blocked ? t.dim : t.foreground;
    const main = h_flex().id(`task-${id}`).h(28).pl(8).pr(4).gap(7).flex_none().items_center().rounded(6)
      .cursor_pointer().when(open, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover)).tooltip(tip)
      .on_click((_e, cx) => { open ? this.expanded.delete(id) : this.expanded.add(id); cx.notify(); })
      .child(h_flex().w(20).flex_none().justify_center().child(this.badge(t, task, live)))
      .child(div().flex_none().text_size(11).text_color(t.dim).child(id))
      .child(div().flex_1().min_w(0).truncate().text_color(titleColor).child(task.title))
      .when(blocked, (el) => el.child(div().flex_none().text_size(10.5).text_color(t.dim).child(`czeka na: ${task.waiting.join(", ")}`)))
      .when(Boolean(this.pill(t, task, live)), (el) => el.child(this.pill(t, task, live)))
      .child(h_flex().flex_none().gap(1).items_center().children(actions));
    const out = [main];
    if (open) out.push(div().ml(35).mr(4).mb(4).p(8).rounded(6).bg(t.surface).border(1).border_color(t.border)
      .text_size(12).text_color(t.muted)
      .child(task.brief?.trim() ? TextView.markdown(`brief-${id}`, task.brief).selectable() : div().text_color(t.dim).child("Brak treści.")));
    return this.conflict[id] ? [...out, this.conflictBox(t, id)] : out;
  }

  render(cx) {
    const t = theme();
    const terms = terminals();
    const agentList = agents();
    const activeSpace = spaces().find((s) => s.active)?.name?.toLowerCase();
    const shown = this.tasks.filter((x) => this.showDone || x.status !== "done");
    const count = (s) => this.tasks.filter((x) => x.status === s).length;

    const groups = new Map();
    for (const task of shown) {
      if (!groups.has(task.project)) groups.set(task.project, []);
      groups.get(task.project).push(task);
    }
    const urgency = (list) => Math.max(...list.map((x) => RANK[x.status === "running" ? this.live(x, terms) : x.status === "done" ? "finished" : x.status] ?? 0));
    const order = [...groups.entries()].sort(([a, la], [b, lb]) =>
      (b.toLowerCase() === activeSpace) - (a.toLowerCase() === activeSpace) || urgency(lb) - urgency(la) || a.localeCompare(b));

    const header = h_flex().h(34).px(10).gap(8).flex_none().items_center().border_b(1).border_color(t.border)
      .child(div().font_semibold().child("Zadania"))
      .child(div().flex_1().truncate().text_size(11).text_color(t.dim)
        .child(`${count("todo")} do zrobienia · ${count("running")} w toku · ${count("review")} do przeglądu`))
      .child(div().id("toggle-done").px(6).h(20).rounded(5).text_size(11).flex().items_center().cursor_pointer()
        .text_color(this.showDone ? t.foreground : t.dim).when(this.showDone, (el) => el.bg(t.selected))
        .hover((s) => s.bg(t.hover)).tooltip("Pokaż zakończone")
        .on_click((_e, cx) => { this.showDone = !this.showDone; cx.notify(); }).child(`✓ ${count("done")}`))
      .child(this.icon(t, "refresh", ICONS + "refresh.svg", "Odśwież", (cx) => cx.spawn(async (cx) => { await this.load(); cx.notify(); })));

    const items = [];
    if (this.error) items.push(div().m(6).p(8).rounded(6).bg(t.blocked + "1f").text_size(11.5).text_color(t.blocked).child(this.error));
    if (!order.length && !this.error) {
      items.push(div().p(12).text_size(12).text_color(t.dim).child("Brak zadań. Napisz do orkiestratora poniżej, co trzeba zrobić."));
    }
    for (const [project, list] of order) {
      const open = !this.folded.has(project);
      const key = `proj:${project}`;
      const auto = this.tasks.some((x) => x.project === project && x.autopilot);
      const chip = (id, label, tip, on, action) => div().id(id).flex_none().px(6).h(18).rounded(5).text_size(10.5).flex().items_center()
        .cursor_pointer().tooltip(tip).text_color(on ? t.accent : t.dim).bg(on ? t.accent + "1f" : t.background)
        .hover((s) => s.bg(t.selected)).on_click((_e, cx) => { cx.stop_propagation(); action(cx); }).child(label);
      items.push(h_flex().id(`proj-${project}`).h(26).px(6).mt(4).gap(5).flex_none().items_center().rounded(6).cursor_pointer()
        .hover((s) => s.bg(t.hover))
        .on_click((_e, cx) => { open ? this.folded.add(project) : this.folded.delete(project); cx.notify(); })
        .child(svg(`mods/workspace-chrome/chevron-${open ? "down" : "right"}.svg`).size(11).flex_none().text_color(t.dim))
        .child(div().flex_1().truncate().text_size(11.5).font_semibold()
          .text_color(project.toLowerCase() === activeSpace ? t.foreground : t.muted).child(project))
        .when(Boolean(this.busy[key]), (el) => el.child(div().text_size(11).text_color(t.dim).child(this.busy[key])))
        .child(chip(`auto-${project}`, "auto", auto ? "Autopilot włączony: po done startuje to, co stało się gotowe" :
          "Autopilot wyłączony", auto, (cx) => this.act(cx, key, ["project", project, "--autopilot", auto ? "off" : "on"], "…")))
        .child(div().text_size(11).text_color(t.dim).child(String(list.length))));
      if (!open) continue;
      // Kolejne etapy (fale z orch). Etap z kilkoma zadaniami = grupa równoległa z własnym startem.
      for (let i = 0; i < list.length;) {
        const wave = list[i].wave;
        let j = i;
        while (j < list.length && list[j].wave === wave) j++;
        const stage = list.slice(i, j);
        i = j;
        const rows = stage.flatMap((task) => this.row(t, task, this.live(task, terms), agentList));
        if (stage.length < 2) { items.push(...rows); continue; }
        items.push(this.group(t, project, wave, stage, rows));
      }
    }

    const body = v_flex().id("task-list").flex_1().min_h(0).px(4).py(4).gap(1).overflow_y_scrollbar().children(items);
    return v_flex().size_full().bg(t.background).text_size(12.5).text_color(t.foreground).child(header).child(body);
  }
}
