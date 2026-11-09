#!/usr/bin/env python
"""orch: projekty i zadania dla orkiestratora herdr.

Zadania żyją w dwóch miejscach: <projekt>/TASKS.md (ważniejsze, repo wygrywa)
i centralnie w tasks.json (lustro + dane uruchomieniowe: terminal, worktree, historia).
"""
import argparse, difflib, json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("ORCH_ROOT", "D:/Projects"))
PROJECTS = HERE / "projects.json"
TASKS = HERE / "tasks.json"
STATUSES = ("todo", "running", "review", "done")
AGENTS = {"claude": ["claude"], "codex": ["codex"]}
REPO_FIELDS = ("title", "status", "agent", "created", "branch", "after")
ORCH = f'python "{(HERE / "orch.py").as_posix()}"'
HERDR = json.loads(os.environ.get("ORCH_HERDR", '["herdr-app"]'))  # testy podmieniają na atrapę
HEAD = re.compile(r"^## (T-\d+|T-\?)\s*·\s*(.+?)\s*$")
META = re.compile(r"^- (\w+):\s*(.*?)\s*$")

for s in (sys.stdout, sys.stderr, sys.stdin):
    if s:
        s.reconfigure(encoding="utf-8")


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def read_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_atomic(p, text):
    # ponytail: brak blokady między procesami; zapisy są rzadkie, os.replace chroni przed uciętym plikiem
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def write_json(p, data):
    write_atomic(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def text_arg(v):
    return sys.stdin.read().strip() if v == "-" else (v or "")


# ---------- TASKS.md ----------

def parse_md(text):
    tasks, cur, in_meta = [], None, False
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            cur = {"id": None if m[1] == "T-?" else m[1], "title": m[2], "brief": [], "after": []}
            tasks.append(cur)
            in_meta = True
            continue
        if cur is None:
            continue
        if in_meta:
            mm = META.match(line)
            if mm and mm[1] in REPO_FIELDS:
                cur[mm[1]] = ids(mm[2]) if mm[1] == "after" else mm[2]
                continue
            if not line.strip():
                continue
            in_meta = False
        cur["brief"].append(line)
    for t in tasks:
        t["brief"] = "\n".join(t["brief"]).strip()
    return tasks


def num(tid):
    return int(tid.split("-")[1])


def ids(text):
    """'T-6, 7 t-8' -> ['T-6', 'T-7', 'T-8']"""
    return [x.upper() if x.upper().startswith("T-") else f"T-{x}" for x in re.split(r"[,\s]+", text or "") if x]


def render_md(project, items):
    out = [f"# Zadania: {project}", "",
           "<!-- Zarządzane przez orkiestratora (orch.py). Można edytować ręcznie w tym formacie.",
           "     Nowe zadanie: nagłówek \"## T-? · tytuł\", ID zostanie nadane przy następnym odczycie. -->", ""]
    for tid, t in sorted(items.items(), key=lambda kv: (STATUSES.index(kv[1]["status"]) if kv[1]["status"] in STATUSES else 9, num(kv[0]))):
        out.append(f"## {tid} · {t['title']}")
        out += [f"- {k}: {', '.join(t[k]) if k == 'after' else t[k]}" for k in REPO_FIELDS[1:] if t.get(k)]
        out.append("")
        if t.get("brief"):
            out += [t["brief"], ""]
    return "\n".join(out)


# ---------- stan ----------

def new_id(db):
    n = db["next_id"]
    db["next_id"] += 1
    return f"T-{n}"


def load():
    """Czyta oba magazyny. TASKS.md w repo wygrywa z kopią centralną."""
    projects = read_json(PROJECTS, {"projects": {}})
    db = read_json(TASKS, {"next_id": 1, "tasks": {}})
    dirty = set()
    for name, p in projects["projects"].items():
        f = Path(p["path"]) / "TASKS.md"
        if not f.exists():
            # ponytail: brak pliku = odtwórz z kopii centralnej (nie gubimy zadań); świadome czyszczenie: orch rm
            if any(t["project"] == name for t in db["tasks"].values()):
                dirty.add(name)
            continue
        parsed = parse_md(f.read_text(encoding="utf-8"))
        for t in parsed:
            if t["id"]:
                db["next_id"] = max(db["next_id"], num(t["id"]) + 1)
        seen = set()
        for t in parsed:
            raw = t.pop("id")
            tid = raw or new_id(db)
            if not raw:
                dirty.add(name)  # "T-?" dostało ID, trzeba je zapisać w pliku
            cur = db["tasks"].setdefault(tid, {"status": "todo", "agent": "claude", "created": now()[:10], "history": [[now(), "dodane w TASKS.md"]]})
            cur.update(t, project=name)
            seen.add(tid)
        for tid in [k for k, t in db["tasks"].items() if t["project"] == name and k not in seen]:
            del db["tasks"][tid]
    return projects, db, dirty


def save(projects, db, touched):
    write_json(TASKS, db)
    for name in touched:
        p = projects["projects"].get(name)
        if p and Path(p["path"]).is_dir():
            items = {k: t for k, t in db["tasks"].items() if t["project"] == name}
            write_atomic(Path(p["path"]) / "TASKS.md", render_md(name, items))


def graph(db):
    """id -> {wave, waiting, ready, parallel}. Fala = 1 + najwyższa fala poprzedników."""
    tasks, waves = db["tasks"], {}

    def wave(tid, path=()):
        if tid not in waves:
            # ponytail: cykl z ręcznej edycji TASKS.md nie wiesza listy, tylko jest ucinany; add/edit cykli nie przepuszcza
            deps = [d for d in tasks[tid].get("after", []) if d in tasks and d not in path]
            waves[tid] = 1 + max((wave(d, path + (tid,)) for d in deps), default=0)
        return waves[tid]

    out = {}
    for tid, t in tasks.items():
        waiting = [d for d in t.get("after", []) if tasks.get(d, {}).get("status") != "done"]
        out[tid] = {"wave": wave(tid), "waiting": waiting, "ready": t["status"] == "todo" and not waiting}
    for tid, g in out.items():
        t = tasks[tid]
        g["parallel"] = t["status"] != "done" and any(
            k != tid and x["project"] == t["project"] and x["status"] != "done" and out[k]["wave"] == g["wave"]
            for k, x in tasks.items())
    return out


def check_after(db, tid, after):
    for d in after:
        if d not in db["tasks"]:
            die(f"--after: nie ma zadania {d}.")
        if d == tid:
            die(f"--after: {tid} nie może zależeć od siebie.")
    # cykl: czy od któregoś poprzednika da się dojść z powrotem do tid
    stack, seen = list(after), set()
    while stack:
        d = stack.pop()
        if d == tid:
            die(f"--after: zależność tworzy cykl przez {tid}.")
        if d not in seen and d in db["tasks"]:
            seen.add(d)
            stack += db["tasks"][d].get("after", [])


def busy_in(db, project, skip=()):
    return [k for k, x in db["tasks"].items()
            if k not in skip and x["project"] == project and x["status"] in ("running", "review")]


def project_name(projects, q):
    names = list(projects["projects"])
    for n in names:
        if n.lower() == q.lower():
            return n
    hint = difflib.get_close_matches(q, names, n=5, cutoff=0.3)
    die(f"Nie ma projektu '{q}'." + (f" Może: {', '.join(hint)}?" if hint else " Zobacz: orch projects"))


def task(db, tid):
    tid = tid.upper() if tid.upper().startswith("T-") else f"T-{tid}"
    if tid not in db["tasks"]:
        die(f"Nie ma zadania {tid}.")
    return tid, db["tasks"][tid]


def herdr_ls():
    r = run([*HERDR, "ls"])
    try:
        return json.loads(r.stdout)["spaces"]
    except (json.JSONDecodeError, KeyError):
        return None


def live():
    """terminal -> status agenta; None gdy herdr nie odpowiada."""
    spaces = herdr_ls()
    if spaces is None:
        return None
    return {term["terminal"]: term["status"] for s in spaces for tab in s["tabs"] for term in tab["terminals"]}


def live_str(t, alive):
    if t["status"] != "running" or not t.get("terminal"):
        return ""
    if alive is None:
        return f"[{t['terminal']}: ?]"
    if t["terminal"] not in alive:
        return f"[{t['terminal']}: zakończony]"
    return f"[{t['terminal']}: {alive[t['terminal']] or 'uruchamia się'}]"


def herdr(cmd, args):
    if "task" in args:  # ponytail: obejście błędu herdr - na Windows nie escapuje U+0022 w argumencie, wiadomość ucina się na pierwszej spacji poza cytowaniem
        args = {**args, "task": args["task"].replace('"', "'")}
    r = run([*HERDR, "do", cmd, json.dumps(args, ensure_ascii=False)])
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        die(f"herdr-app do {cmd}: {r.stdout}{r.stderr}".strip())
    if r.returncode or "error" in out:
        die(f"herdr-app do {cmd}: {out.get('error', r.stderr)}")
    return out


def same_dir(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def find_space(name):
    # ponytail: tylko po nazwie; folder space'u w herdr nie mówi, czyj jest (space "inshootsanity" miał folder magnatV2)
    return next((s["id"] for s in herdr_ls() or [] if not s.get("machine") and s["name"].lower() == name.lower()), None)


def slug(s):
    s = s.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:30] or "zadanie"


def describe(path):
    try:
        d = json.loads((path / "package.json").read_text(encoding="utf-8")).get("description")
        if d:
            return d
    except (OSError, ValueError, AttributeError):
        pass
    for n in ("README.md", "CLAUDE.md"):
        f = path / n
        if f.exists():
            for para in re.split(r"\n\s*\n", f.read_text(encoding="utf-8", errors="replace")):
                s = para.strip()
                if s and not s.startswith(("#", "<", "!", "[", "---", "```", "|")):
                    return re.sub(r"\s+", " ", s)[:300]
    return ""


# ---------- polecenia: projekty ----------

def cmd_projects(a):
    projects, db, _ = load()
    rows = []
    for name, p in sorted(projects["projects"].items(), key=lambda kv: kv[0].lower()):
        if p.get("archived") and not a.all:
            continue
        last = run(["git", "-C", p["path"], "log", "-1", "--format=%cs"]).stdout.strip()
        open_ = [k for k, t in db["tasks"].items() if t["project"] == name and t["status"] != "done"]
        rows.append({"name": name, **p, "last_commit": last, "open_tasks": open_})
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    for r in rows:
        flags = " [archiwum]" if r.get("archived") else ""
        kw = f" | słowa: {', '.join(r['keywords'])}" if r.get("keywords") else ""
        print(f"{r['name']}{flags}  ({r['path']}, ostatni commit {r['last_commit'] or '-'}, otwarte: {len(r['open_tasks'])})")
        print(f"    {r.get('desc') or '(brak opisu)'}{kw}")


def cmd_project(a):
    projects, _, _ = load()
    ps = projects["projects"]
    name = next((n for n in ps if n.lower() == a.name.lower()), a.name)
    p = ps.setdefault(name, {"path": str(ROOT / name), "desc": ""})
    if a.path:
        p["path"] = str(Path(a.path).resolve())
    if a.desc is not None:
        p["desc"] = text_arg(a.desc)
    if a.keywords is not None:
        p["keywords"] = [k.strip() for k in a.keywords.split(",") if k.strip()]
    if a.archived is not None:
        p["archived"] = a.archived
    if a.autopilot:
        p["autopilot"] = a.autopilot == "on"
    if a.rename:
        ps[a.rename] = ps.pop(name)
        _, db, _ = load()
        for t in db["tasks"].values():
            if t["project"] == name:
                t["project"] = a.rename
        write_json(TASKS, db)
        name = a.rename
    if not Path(p["path"]).is_dir():
        die(f"Folder {p['path']} nie istnieje (nowy projekt: orch new).")
    write_json(PROJECTS, projects)
    print(f"{name}: {p['path']}" + (" [autopilot]" if p.get("autopilot") else "") + f"\n    {p.get('desc') or '(brak opisu)'}")


def cmd_scan(a):
    projects, _, _ = load()
    added = []
    for d in sorted(ROOT.iterdir(), key=lambda d: d.name.lower()):
        # .git jako katalog = repo; .git jako plik = worktree, należy do repo-rodzica
        if d.name.startswith((".", "_")) or not (d / ".git").is_dir():
            continue
        if any(same_dir(p["path"], d) for p in projects["projects"].values()):
            continue
        projects["projects"][d.name] = {"path": str(d), "desc": describe(d)}
        added.append(d.name)
    write_json(PROJECTS, projects)
    print(f"Dodano {len(added)}: {', '.join(added)}" if added else "Nic nowego.")


def cmd_new(a):
    projects, _, _ = load()
    if any(n.lower() == a.name.lower() for n in projects["projects"]):
        die(f"Projekt {a.name} już jest w rejestrze.")
    path = Path(a.path).resolve() if a.path else ROOT / a.name
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        run(["git", "init", "-q", str(path)])
    desc = text_arg(a.desc)
    idea = path / "IDEA.md"
    if not idea.exists():
        idea.write_text(f"# {a.name}\n\n> {desc or 'Pomysł do rozwinięcia.'}\n\n## Notatki z brainstormingu\n\n", encoding="utf-8", newline="\n")
    projects["projects"][a.name] = {"path": str(path), "desc": desc}
    write_json(PROJECTS, projects)
    print(f"Utworzono {a.name} w {path} (git, IDEA.md).")


def cmd_idea(a):
    projects, _, _ = load()
    name = project_name(projects, a.project)
    f = Path(projects["projects"][name]["path"]) / "IDEA.md"
    if a.set is None and a.append is None:
        print(f.read_text(encoding="utf-8") if f.exists() else f"(brak {f})")
        return
    if a.set is not None:
        f.write_text(text_arg(a.set) + "\n", encoding="utf-8", newline="\n")
    else:
        old = f.read_text(encoding="utf-8") if f.exists() else f"# {name}\n"
        f.write_text(old.rstrip("\n") + "\n\n" + text_arg(a.append) + "\n", encoding="utf-8", newline="\n")
    print(f"Zapisano {f}")


# ---------- polecenia: zadania ----------

def cmd_add(a):
    projects, db, dirty = load()
    name = project_name(projects, a.project)
    after = ids(a.after)
    check_after(db, None, after)
    tid = new_id(db)
    db["tasks"][tid] = {"project": name, "title": a.title, "status": "todo", "agent": a.agent, "after": after,
                        "created": now()[:10], "brief": text_arg(a.brief), "history": [[now(), "utworzone"]]}
    save(projects, db, dirty | {name})
    print(f"{tid} dodane do {name} jako TODO.")
    if a.start:
        a.id = tid
        cmd_start(a)


def cmd_list(a):
    projects, db, dirty = load()
    if dirty:
        save(projects, db, dirty)
    name = project_name(projects, a.project) if a.project else None
    alive = live()
    g = graph(db)
    rows = [(k, t) for k, t in sorted(db["tasks"].items(), key=lambda kv: (g[kv[0]]["wave"], num(kv[0])))
            if (not name or t["project"] == name)
            and (t["status"] == a.status if a.status else a.all or t["status"] != "done")]
    ps = projects["projects"]
    if a.json:
        print(json.dumps([{"id": k, **t, **g[k], "autopilot": bool(ps.get(t["project"], {}).get("autopilot")),
                           "live": (alive or {}).get(t.get("terminal")) if alive is not None else None}
                          for k, t in rows], ensure_ascii=False, indent=1))
        return
    if not rows:
        print("Brak zadań.")
    for proj in sorted({t["project"] for _, t in rows}, key=str.lower):
        print(proj + (" [autopilot]" if ps.get(proj, {}).get("autopilot") else ""))
        for k, t in rows:
            if t["project"] == proj:
                x = g[k]
                wave = f"F{x['wave']}" + ("∥" if x["parallel"] else "")
                state = (f"czeka na: {', '.join(x['waiting'])}" if t["status"] == "todo" and x["waiting"] else
                         "gotowe" if x["ready"] else "")
                coord = f"(koordynator: {', '.join(t['coordinator'])})" if t["status"] == "running" and t.get("coordinator") else ""
                print(f"  {k:<6} {wave:<4} {t['status']:<7} {t['title']}  {state} {coord} {live_str(t, alive)}".rstrip())


def cmd_show(a):
    projects, db, _ = load()
    tid, t = task(db, a.id)
    print(f"{tid} · {t['title']}")
    print(f"projekt: {t['project']} ({projects['projects'].get(t['project'], {}).get('path', '?')})")
    print(f"status: {t['status']} {live_str(t, live())}  agent: {t.get('agent', 'claude')}  utworzone: {t.get('created', '-')}")
    x = graph(db)[tid]
    print(f"fala: {x['wave']}" + (" (równolegle z innymi)" if x["parallel"] else "") +
          (f"  po: {', '.join(t['after'])}" if t.get("after") else "") +
          (f"  czeka na: {', '.join(x['waiting'])}" if t["status"] == "todo" and x["waiting"] else ""))
    for k in ("branch", "worktree", "space", "tab", "terminal", "coordinator"):
        if t.get(k):
            print(f"{k}: {', '.join(t[k]) if isinstance(t[k], list) else t[k]}")
    print("\n" + (t.get("brief") or "(brak opisu)"))
    if t.get("history"):
        print("\nhistoria:")
        for when, what in t["history"]:
            print(f"  {when}  {what}")


def cmd_edit(a):
    projects, db, dirty = load()
    tid, t = task(db, a.id)
    touched = dirty | {t["project"]}
    changes = []
    if a.title:
        t["title"] = a.title
        changes.append("tytuł")
    if a.brief is not None:
        t["brief"] = text_arg(a.brief)
        changes.append("opis")
    if a.append is not None:
        t["brief"] = (t.get("brief", "") + "\n\n" + text_arg(a.append)).strip()
        changes.append("dopisano do opisu")
    if a.agent:
        t["agent"] = a.agent
        changes.append(f"agent → {a.agent}")
    if a.project:
        t["project"] = project_name(projects, a.project)
        touched.add(t["project"])
        changes.append(f"projekt → {t['project']}")
    if a.after is not None:
        t["after"] = ids(a.after)
        check_after(db, tid, t["after"])
        changes.append(f"po → {', '.join(t['after']) or '(brak)'}")
    finished = a.status == "done" and t["status"] != "done"
    folder = projects["projects"][t["project"]]["path"]
    if finished and not a.force:
        require_clean(tid, t, folder)
    if a.status:
        t["status"] = a.status
        changes.append(f"status → {a.status}")
    if a.note:
        changes.append(a.note)
    if finished:
        close_window(db, tid, t)
        if t.get("worktree") and (gone := remove_worktree(t, folder)):
            changes.append(gone)
    if not changes:
        die("Nic do zmiany.")
    t.setdefault("history", []).append([now(), "; ".join(changes)])
    save(projects, db, touched)
    print(f"{tid}: {'; '.join(changes)}")
    if t["status"] == "running" and (a.brief is not None or a.append is not None) and t.get("terminal"):
        print(f"Uwaga: zadanie działa w {t['terminal']}, agent nie widzi zmiany opisu. Przekaż mu ją: hc send {t['terminal']} \"...\"")
    if finished and projects["projects"].get(t["project"], {}).get("autopilot"):
        autopilot(t["project"])


def uncommitted(folder):
    # ponytail: TASKS.md i IDEA.md pisze orch, nie agent; poza repo (albo po usuniętym worktree) nic do commitu
    r = run(["git", "-C", folder, "status", "--porcelain", "--", ".", ":(exclude)TASKS.md", ":(exclude)IDEA.md"])
    return [] if r.returncode else r.stdout.rstrip().splitlines()


def unmerged(folder, branch):
    """Commity gałęzi zadania, których nie ma w głównym checkoucie (HEAD); brak gałęzi = nic."""
    r = run(["git", "-C", folder, "log", "--oneline", f"HEAD..{branch}"]) if branch else None
    return [] if not r or r.returncode else r.stdout.rstrip().splitlines()


def require_clean(tid, t, folder):
    """Przed done: niezakomitowane zmiany (a przy worktree także niescalone commity) = kod 4,
    status i okno bez zmian, aż użytkownik zdecyduje."""
    wt = t.get("worktree")
    files = uncommitted(wt or folder)
    commits = unmerged(folder, t.get("branch")) if wt else []
    if not files and not commits:
        return
    cut = lambda xs: xs[:20] + ([f"... i {len(xs) - 20} więcej"] if len(xs) > 20 else [])
    lines = (([f"niezakomitowane zmiany w {wt or folder}:"] + cut(files) if files else []) +
             ([f"niescalone z główną gałęzią commity {t.get('branch')}:"] + cut(commits) if commits else []))
    ask = ("Zapytaj użytkownika: commit i scalenie do głównej gałęzi czy zostawić zmiany na worktree?" if wt else
           "Zapytaj użytkownika, czy zrobić commit.")
    die(f"{tid}: zostaje {t['status']}, okno otwarte.\n" + "\n".join(lines) + f"\n{ask}\nZostawić zmiany i zamknąć: --force.", 4)


def remove_worktree(t, folder):
    """Po done: czysty i scalony worktree znika razem z gałęzią; zmiany zostawione przez --force zostają."""
    wt, branch = t["worktree"], t.get("branch")
    if not Path(wt).exists() or uncommitted(wt) or unmerged(folder, branch):
        return None
    for _ in range(5):  # ponytail: na Windows dopiero co zabity terminal chwilę trzyma folder; 5 prób co 1 s
        r = run(["git", "-C", folder, "worktree", "remove", wt])
        if not r.returncode:
            break
        time.sleep(1)
    else:
        print(f"Nie usunięto worktree: {r.stderr.strip()}\nRęcznie: git -C {folder} worktree remove {wt}")
        return None
    if branch:
        run(["git", "-C", folder, "branch", "-d", branch])
    run(["git", "-C", folder, "worktree", "prune"])
    t.pop("worktree")
    t.pop("branch", None)
    return f"usunięto worktree {wt}" + (f" i gałąź {branch}" if branch else "")


def close_window(db, tid, t):
    """Po done kończy terminal agenta; herdr sam zamyka pusty panel, kartę i space. Koordynator: po ostatnim zadaniu."""
    term = t.get("terminal")
    if not term or any(k != tid and x.get("terminal") == term and x["status"] in ("running", "review") for k, x in db["tasks"].items()):
        return
    if term in (live() or {}):
        try:
            herdr("terminal.kill", {"terminal": term})
            print(f"Zamknięto okno zadania ({term}).")
        except SystemExit:
            pass  # komunikat już na stderr; samo edit się udało


def autopilot(project):
    """Po done: gotowe zadania z zależnościami startują same (jedno zwykle, kilka przez koordynatora).
    Niezależne TODO zostają dla użytkownika. Czeka, póki w projekcie coś trwa albo czeka na review."""
    _, db, _ = load()
    g = graph(db)
    ready = sorted((k for k, x in db["tasks"].items() if x["project"] == project and g[k]["ready"] and x.get("after")), key=num)
    if not ready:
        return
    busy = busy_in(db, project)
    if busy:
        print(f"autopilot: gotowe {', '.join(ready)}, ale w projekcie trwa albo czeka na review: {', '.join(busy)}. Ruszy po ich done.")
        return
    print(f"autopilot: startuję {', '.join(ready)}")
    try:
        start_many(project, ready, here=False)
    except SystemExit:
        pass  # komunikat już na stderr; samo edit się udało


def cmd_rm(a):
    projects, db, dirty = load()
    tid, t = task(db, a.id)
    del db["tasks"][tid]
    save(projects, db, dirty | {t["project"]})
    print(f"Usunięto {tid}.")


def brief_message(tid, t):
    return (f"[{tid}] {t['title']}\n\n{t.get('brief') or '(bez dodatkowego opisu: tytuł mówi wszystko)'}\n\n---\n"
            f"To zadanie {tid} z orkiestratora (projekt {t['project']}). Gdy skończysz i zmiany będą gotowe do przeglądu, zgłoś to:\n"
            f'{ORCH} edit {tid} --status review --note "<jedno zdanie: co zrobiono>"\n'
            f"Jeśli potrzebujesz decyzji, zapytaj tutaj. Nie edytuj ręcznie TASKS.md.")


def cmd_start(a):
    projects, db, dirty = load()
    tid, t = task(db, a.id)
    p = projects["projects"][t["project"]]
    alive = live() or {}
    if t["status"] == "running" and t.get("terminal") in alive:
        die(f"{tid} już działa w {t['terminal']}.")
    if t["status"] in ("done", "review") and not a.force:
        die(f"{tid} ma status {t['status']}. Uruchom mimo to: --force.")
    waiting = graph(db)[tid]["waiting"]
    if waiting and not a.force:
        die(f"{tid} jest zablokowane, czeka na: {', '.join(waiting)} (jeszcze nie done).\n"
            "Uruchom mimo to: --force.", 3)
    busy = busy_in(db, t["project"], skip=(tid,))
    if busy and not (a.worktree or a.here):
        die(f"W projekcie {t['project']} trwa albo czeka na review: {', '.join(busy)}.\n"
            "Sugestia: osobny worktree (--worktree) albo świadomie w tym samym checkoucie (--here).", 2)
    folder, tab = p["path"], f"{tid} {t['title']}"[:40]
    args = {"argv": AGENTS[t.get("agent", "claude")], "task": brief_message(tid, t)}
    if a.worktree:
        branch = t.get("branch") or f"task/{tid.lower()}-{slug(t['title'])}"
        wt = Path(t.get("worktree") or Path.home() / ".herdr" / "worktrees" / Path(folder).name / branch)
        if not wt.exists():
            has = run(["git", "-C", folder, "branch", "--list", branch]).stdout.strip()
            r = run(["git", "-C", folder, "worktree", "add", *(["-b", branch, str(wt)] if not has else [str(wt), branch])])
            if r.returncode:
                die(f"git worktree add: {r.stderr.strip()}")
        t.update(branch=branch, worktree=str(wt))
        # bez nazwy: sidebar herdr zagnieździ worktree pod space'em repo
        out = herdr("space.new", {"folder": str(wt), "tab": tab, "machine": "", **args})
    else:
        sid = find_space(t["project"])
        out = (herdr("tab.new", {"space": sid, "folder": folder, "name": tab, **args}) if sid else
               herdr("space.new", {"name": t["project"], "folder": folder, "tab": tab, "machine": "", **args}))
    t.pop("coordinator", None)
    t.update(status="running", terminal=out.get("terminal"), space=out.get("space"), tab=out.get("tab"))
    t.setdefault("history", []).append([now(), f"start: {t.get('agent', 'claude')} w {out.get('terminal')}" + (" (worktree)" if a.worktree else "")])
    save(projects, db, dirty | {t["project"]})
    print(f"{tid} wystartowane: terminal {out.get('terminal')}" + (f", worktree {t['worktree']}" if a.worktree else ""))


def cmd_start_any(a):
    if len(a.id) == 1 and not a.ready:
        a.id = a.id[0]
        return cmd_start(a)
    if not a.id and not a.ready:
        die("Podaj ID zadania (kilka = jedna równoległa sesja koordynatora) albo --ready <projekt>.")
    projects, db, _ = load()
    g = graph(db)
    if a.ready:
        name = project_name(projects, a.ready)
        ready = sorted((k for k, x in db["tasks"].items() if x["project"] == name and g[k]["ready"]), key=num)
        if not ready:
            die(f"W {name} nie ma gotowych zadań (TODO bez niezakończonych poprzedników).")
    else:
        ready = [task(db, x)[0] for x in a.id]
        names = {db["tasks"][k]["project"] for k in ready}
        if len(names) > 1:
            die(f"Równoległa sesja obejmuje jeden projekt, a tu: {', '.join(sorted(names))}.")
        name = names.pop()
        if bad := [k for k in ready if db["tasks"][k]["status"] != "todo"]:
            die(f"Nie są w TODO: {', '.join(bad)}.")
        if (blocked := [k for k in ready if g[k]["waiting"]]) and not a.force:
            why = "; ".join(k + " czeka na " + ", ".join(g[k]["waiting"]) for k in blocked)
            die(f"Zablokowane: {why}.\nUruchom mimo to: --force.", 3)
    busy = busy_in(db, name, skip=ready)
    if busy and not a.here:
        die(f"W projekcie {name} trwa albo czeka na review: {', '.join(busy)}.\n"
            "Koordynator pracuje w głównym checkoucie; świadomie mimo to: --here.", 2)
    start_many(name, ready, here=True)


def start_many(project, tids, here):
    if len(tids) == 1:
        cmd_start(argparse.Namespace(id=tids[0], worktree=False, here=here, force=False))
    else:
        start_coordinator(project, tids)


def coordinator_message(project, path, branch, tids, db):
    main_ = f"`{branch}`" if branch else "gałęzi głównej"
    parts = [f"[Koordynator] {project}: {', '.join(tids)}", "",
             f"Jesteś koordynatorem zadań {', '.join(tids)} w projekcie {project} ({path}). "
             "Nie zależą od siebie nawzajem, więc mogą iść równolegle. Twoja praca:",
             "1. Każde zadanie zleć osobnemu subagentowi w osobnym git worktree (np. Agent z isolation: \"worktree\" "
             "albo `git worktree add` z gałęzią task/<id>-...) i puść je równolegle. Subagenci nie zgłaszają statusu do orkiestratora.",
             f"2. Gdy zadanie jest gotowe: przejrzyj zmiany i scal jego gałąź do {main_} w tym checkoucie. "
             "Rozwiąż konflikty; po każdym scaleniu uruchom build i testy projektu i napraw, co się zepsuło.",
             "3. Po udanym scaleniu usuń worktree (`git worktree remove <ścieżka>`) i gałąź zadania (`git branch -d <gałąź>`), "
             "na końcu `git worktree prune`. `git worktree list` ma wtedy pokazywać tylko główny checkout.",
             f'4. Dla każdego scalonego zadania zgłoś: {ORCH} edit <ID> --status review --note "<jedno zdanie: co zrobiono>"',
             "Jeśli potrzebujesz decyzji, zapytaj tutaj. Nie edytuj ręcznie TASKS.md.", ""]
    for tid in tids:
        t = db["tasks"][tid]
        extra = " (wymaga generowania obrazów: użyj Codexa)" if t.get("agent", "claude") != "claude" else ""
        parts += ["---", f"## {tid} · {t['title']}{extra}", "",
                  t.get("brief") or "(bez dodatkowego opisu: tytuł mówi wszystko)", ""]
    return "\n".join(parts)


def start_coordinator(project, tids):
    projects, db, dirty = load()
    folder = projects["projects"][project]["path"]
    branch = run(["git", "-C", folder, "branch", "--show-current"]).stdout.strip()
    args = {"argv": AGENTS["claude"], "task": coordinator_message(project, folder, branch, tids, db)}
    tab = f"Koordynator {', '.join(tids)}"[:40]
    sid = find_space(project)
    out = (herdr("tab.new", {"space": sid, "folder": folder, "name": tab, **args}) if sid else
           herdr("space.new", {"name": project, "folder": folder, "tab": tab, "machine": "", **args}))
    for tid in tids:
        t = db["tasks"][tid]
        t.update(status="running", terminal=out.get("terminal"), space=out.get("space"), tab=out.get("tab"), coordinator=tids)
        t.setdefault("history", []).append([now(), f"start: koordynator w {out.get('terminal')} ({', '.join(tids)})"])
    save(projects, db, dirty | {project})
    print(f"Koordynator dla {', '.join(tids)} wystartował: terminal {out.get('terminal')}")


def main():
    ap = argparse.ArgumentParser(prog="orch", description="Projekty i zadania orkiestratora herdr. Tekst '-' = czytaj ze stdin.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("projects", help="lista projektów")
    s.add_argument("--all", action="store_true", help="także archiwalne")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_projects)

    s = sp.add_parser("project", help="dodaj/zmień wpis projektu")
    s.add_argument("name")
    s.add_argument("--path")
    s.add_argument("--desc")
    s.add_argument("--keywords", help="po przecinku")
    s.add_argument("--rename")
    s.add_argument("--archived", action="store_true", default=None)
    s.add_argument("--active", dest="archived", action="store_false")
    s.add_argument("--autopilot", choices=("on", "off"), help="po done startuj to, co stało się gotowe")
    s.set_defaults(fn=cmd_project)

    s = sp.add_parser("scan", help=f"zarejestruj nowe repo z {ROOT}")
    s.set_defaults(fn=cmd_scan)

    s = sp.add_parser("new", help="utwórz nowy projekt (folder, git init, IDEA.md)")
    s.add_argument("name")
    s.add_argument("--desc")
    s.add_argument("--path")
    s.set_defaults(fn=cmd_new)

    s = sp.add_parser("idea", help="pokaż/zapisz IDEA.md projektu")
    s.add_argument("project")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--set")
    g.add_argument("--append")
    s.set_defaults(fn=cmd_idea)

    s = sp.add_parser("add", help="nowe zadanie TODO")
    s.add_argument("project")
    s.add_argument("title")
    s.add_argument("--brief")
    s.add_argument("--agent", choices=AGENTS, default="claude")
    s.add_argument("--after", help="zależności po przecinku, np. T-6,T-7")
    s.add_argument("--start", action="store_true", help="od razu uruchom")
    s.add_argument("--worktree", action="store_true")
    s.add_argument("--here", action="store_true", help="uruchom mimo innych zadań w projekcie")
    s.add_argument("--force", action="store_true", help="uruchom mimo niezakończonych zależności")
    s.set_defaults(fn=cmd_add)

    s = sp.add_parser("list", help="lista zadań (bez done)")
    s.add_argument("--project")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--all", action="store_true", help="także done")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sp.add_parser("show", help="szczegóły zadania")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    s = sp.add_parser("edit", help="zmień zadanie")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--brief")
    s.add_argument("--append")
    s.add_argument("--agent", choices=AGENTS)
    s.add_argument("--project")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--after", help="zależności po przecinku; \"\" czyści")
    s.add_argument("--note", help="wpis do historii")
    s.add_argument("--force", action="store_true", help="done mimo niezakomitowanych zmian (zostają, okno się zamyka)")
    s.set_defaults(fn=cmd_edit)

    s = sp.add_parser("start", help="uruchom zadanie w herdr (albo gotowe zadania projektu)")
    s.add_argument("id", nargs="*", help="kilka ID = jedna równoległa sesja koordynatora")
    s.add_argument("--ready", metavar="PROJEKT", help="wszystkie gotowe: jedno zwykle, kilka przez jednego agenta-koordynatora")
    s.add_argument("--worktree", action="store_true")
    s.add_argument("--here", action="store_true")
    s.add_argument("--force", action="store_true", help="mimo niezakończonych zależności")
    s.set_defaults(fn=cmd_start_any)

    s = sp.add_parser("rm", help="usuń zadanie")
    s.add_argument("id")
    s.set_defaults(fn=cmd_rm)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
