"""Test synchronizacji TASKS.md <-> tasks.json na kopii w katalogu tymczasowym. Uruchom: python test_orch.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

src = Path(__file__).resolve().parent / "orch.py"
tmp = Path(tempfile.mkdtemp())
shutil.copy(src, tmp / "orch.py")
# atrapa herdr-app: ls = pusto, do = nowy terminal; każde "do" dopisuje się do herdr.log
(tmp / "fake_herdr.py").write_text(
    "import json, sys\nfrom pathlib import Path\n"
    "if sys.argv[1] == 'ls': sp = Path(__file__).with_name('spaces.json'); print(json.dumps({'spaces': json.loads(sp.read_text()) if sp.exists() else []}))\n"
    "else:\n"
    "    log = Path(__file__).with_name('herdr.log'); n = len(log.read_text().splitlines()) if log.exists() else 0\n"
    "    with log.open('a', encoding='utf-8') as f: f.write(json.dumps([sys.argv[2], json.loads(sys.argv[3])]) + '\\n')\n"
    "    print(json.dumps({'terminal': f't{n}', 'space': 's1', 'tab': f'tab{n}'}))\n", encoding="utf-8")
env = {**os.environ, "ORCH_ROOT": str(tmp / "root"), "ORCH_HERDR": json.dumps([sys.executable, str(tmp / "fake_herdr.py")])}


def orch(*args, stdin=None, code=0):
    r = subprocess.run([sys.executable, str(tmp / "orch.py"), *args], input=stdin, capture_output=True,
                       text=True, encoding="utf-8", env=env)
    assert r.returncode == code, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def herdr_calls():
    f = tmp / "herdr.log"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def db():
    return json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))["tasks"]


orch("new", "demo", "--desc", "Aplikacja do żółwi")
md = tmp / "root" / "demo" / "TASKS.md"
assert (tmp / "root" / "demo" / ".git").is_dir() and (tmp / "root" / "demo" / "IDEA.md").exists()

orch("add", "demo", "Pierwsze zadanie", "--brief", "Opis z ąę")
orch("add", "demo", "Drugie", "--brief", "-", stdin="Wieloliniowy\nopis\n")
assert set(db()) == {"T-1", "T-2"} and db()["T-2"]["brief"] == "Wieloliniowy\nopis"
assert "## T-1 · Pierwsze zadanie" in md.read_text(encoding="utf-8")

# repo wygrywa: ręczna zmiana statusu, usunięcie T-1, nowe zadanie bez ID
text = md.read_text(encoding="utf-8")
text = text.replace("## T-1 · Pierwsze zadanie\n- status: todo", "## T-1 · Pierwsze zadanie\n- status: review")
start = text.index("## T-2")
text = text[:start] + "## T-? · Dopisane ręcznie\n\nZ ręki.\n"
md.write_text(text, encoding="utf-8")
out = orch("list", "--all")
tasks = db()
assert tasks["T-1"]["status"] == "review", tasks
assert "T-2" not in tasks, "usunięte z repo powinno zniknąć z centrali"
assert tasks["T-3"]["title"] == "Dopisane ręcznie" and tasks["T-3"]["brief"] == "Z ręki."
assert "## T-3 · Dopisane ręcznie" in md.read_text(encoding="utf-8"), "nadane ID zapisane w repo"
assert "T-3" in out

orch("edit", "T-3", "--append", "Dopisek", "--status", "done")
assert db()["T-3"]["brief"] == "Z ręki.\n\nDopisek" and db()["T-3"]["status"] == "done"
assert "T-3" not in orch("list"), "done ukryte domyślnie"

md.unlink()  # brak pliku -> odtworzony z centrali
orch("list")
assert "## T-1" in md.read_text(encoding="utf-8")

orch("idea", "demo", "--append", "- pomysł A")
assert "- pomysł A" in orch("idea", "demo")

# ---------- zależności, fale, koordynator, autopilot ----------
orch("new", "graf")
gmd = tmp / "root" / "graf" / "TASKS.md"
for title in ("A", "B", "C", "D", "E"):
    orch("add", "graf", title)  # T-4..T-8
orch("edit", "T-5", "--after", "T-4")
orch("edit", "T-6", "--after", "4")  # samo "4" też działa
orch("edit", "T-7", "--after", "T-5, T-6")
orch("edit", "T-5", "--brief", 'Napisz "hej" i koniec')  # U+0022 nie może dojść do herdr
assert "--after: nie ma zadania T-99" in orch("edit", "T-4", "--after", "T-99", code=1)
assert "cykl" in orch("edit", "T-4", "--after", "T-7", code=1)
assert "- after: T-5, T-6" in gmd.read_text(encoding="utf-8")

# round-trip: ręczna edycja after w TASKS.md
text = gmd.read_text(encoding="utf-8").replace("## T-8 · E\n", "## T-8 · E\n- after: T-7\n")
gmd.write_text(text, encoding="utf-8")
rows = {r["id"]: r for r in json.loads(orch("list", "--project", "graf", "--json"))}
assert rows["T-8"]["after"] == ["T-7"]
assert [rows[k]["wave"] for k in ("T-4", "T-5", "T-6", "T-7", "T-8")] == [1, 2, 2, 3, 4], rows
assert rows["T-5"]["parallel"] and rows["T-6"]["parallel"] and not rows["T-7"]["parallel"]
assert rows["T-4"]["ready"] and not rows["T-5"]["ready"] and rows["T-7"]["waiting"] == ["T-5", "T-6"]
assert "F2∥" in orch("list", "--project", "graf") and "czeka na: T-4" in orch("list", "--project", "graf")
orch("list", "--project", "graf", "--all")
assert gmd.read_text(encoding="utf-8") == text, "round-trip bez zmian"

# start zablokowanego: odmowa bez --force
assert "zablokowane" in orch("start", "T-5", code=3)
assert not herdr_calls()

# start --ready: jedno gotowe -> zwykły start; kilka -> jeden koordynator
orch("start", "--ready", "graf")
assert db()["T-4"]["status"] == "running" and len(herdr_calls()) == 1 and "coordinator" not in db()["T-4"]
orch("edit", "T-4", "--status", "done")
assert len(herdr_calls()) == 1, "autopilot domyślnie wyłączony"
orch("start", "--ready", "graf")
calls = herdr_calls()
assert not any('"' in c[1]["task"] for c in calls) and "Napisz 'hej' i koniec" in calls[1][1]["task"]
assert len(calls) == 2 and "[Koordynator]" in calls[1][1]["task"] and "## T-5" in calls[1][1]["task"] and "## T-6" in calls[1][1]["task"]
t = db()
assert t["T-5"]["coordinator"] == t["T-6"]["coordinator"] == ["T-5", "T-6"] and t["T-5"]["terminal"] == t["T-6"]["terminal"] == "t1"
assert "koordynator: T-5, T-6" in orch("list", "--project", "graf")
assert "nie ma gotowych" in orch("start", "--ready", "graf", code=1)
assert "Nie są w TODO" in orch("start", "T-5", "T-7", code=1)
assert "Zablokowane: T-7 czeka na T-5, T-6" in orch("start", "T-7", "T-8", code=3)
# autopilot: po done ostatniego z koordynatora startuje T-7 zwykłym startem
orch("project", "graf", "--autopilot", "on")
assert json.loads(orch("list", "--project", "graf", "--json"))[0]["autopilot"]
orch("edit", "T-5", "--status", "done")
assert len(herdr_calls()) == 2, "T-6 jeszcze trwa, autopilot czeka"
out = orch("edit", "T-6", "--status", "done")
assert "autopilot: startuję T-7" in out and len(herdr_calls()) == 3 and db()["T-7"]["status"] == "running"
orch("project", "graf", "--autopilot", "off")
# space o cudzej nazwie z folderem projektu nie jest wybierany: nowy space nazwany jak projekt
(tmp / "spaces.json").write_text(json.dumps([{"id": "sX", "name": "inshootsanity", "folder": str(tmp / "root" / "graf"), "tabs": []}]))
orch("add", "graf", "Space", "--start", "--here")
assert herdr_calls()[-1][0] == "space.new" and herdr_calls()[-1][1]["name"] == "graf"
(tmp / "spaces.json").write_text(json.dumps([{"id": "sG", "name": "Graf", "folder": "C:/inny", "tabs": []}]))
orch("add", "graf", "Space 2", "--start", "--here")
assert herdr_calls()[-1][0] == "tab.new" and herdr_calls()[-1][1]["space"] == "sG"

# ---------- done: czyste drzewo zamyka okno, brudne zatrzymuje (kod 4) z właściwym pytaniem ----------
demo = tmp / "root" / "demo"
kills = lambda: [c[1]["terminal"] for c in herdr_calls() if c[0] == "terminal.kill"]
newest = lambda: max(db(), key=lambda k: int(k[2:]))


def started(title):
    orch("add", "demo", title, "--start", "--here")
    tid = newest()
    term = db()[tid]["terminal"]
    (tmp / "spaces.json").write_text(json.dumps([{"id": "sD", "name": "demo", "tabs": [{"terminals": [{"terminal": term, "status": "idle"}]}]}]))
    return tid, term


tid, term = started("Czyste")  # w demo tylko TASKS.md i IDEA.md (pisze je orch), to się nie liczy
assert "Zamknięto okno" in orch("edit", tid, "--status", "done") and kills() == [term]

tid, term = started("Brudne")
(demo / "kod.py").write_text("x = 1")
out = orch("edit", tid, "--status", "done", code=4)
assert "kod.py" in out and "czy zrobić commit" in out and "worktree" not in out, out
assert db()[tid]["status"] == "running" and len(kills()) == 1, "okno zostaje"
orch("edit", tid, "--status", "done", "--force")
assert db()[tid]["status"] == "done" and kills()[-1] == term

tid, term = started("Worktree")
git = lambda *x, cwd=demo: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *x],
                                          check=True, capture_output=True)
wt = tmp / "wt"
git("commit", "-q", "--allow-empty", "-m", "start")
git("worktree", "add", "-q", "-b", "task/x", str(wt))
d = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))
d["tasks"][tid].update(worktree=str(wt), branch="task/x")
(tmp / "tasks.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
(wt / "nowy.py").write_text("y = 2")
out = orch("edit", tid, "--status", "done", code=4)
assert "nowy.py" in out and "scalenie do głównej gałęzi" in out and len(kills()) == 2, out
git("add", "nowy.py", cwd=wt)
git("commit", "-q", "-m", "Nowy plik", cwd=wt)
out = orch("edit", tid, "--status", "done", code=4)  # zakomitowane, ale nie scalone
assert "niescalone" in out and "task/x" in out and "Nowy plik" in out and "nowy.py" not in out.split("niescalone")[0], out
git("merge", "-q", "task/x")  # brudny główny checkout (kod.py) nie blokuje
out = orch("edit", tid, "--status", "done")
assert db()[tid]["status"] == "done" and kills()[-1] == term and "usunięto worktree" in out, out
assert not wt.exists() and "worktree" not in db()[tid] and not git("branch", "--list", "task/x").stdout.strip()

shutil.rmtree(tmp, ignore_errors=True)
print("OK")
