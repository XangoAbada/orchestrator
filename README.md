# orchestrator

Orkiestrator projektów dla [herdr](https://herdr.dev) i Claude Code. Rozmawiasz z jednym agentem Claude Code (orkiestratorem), a on zamienia sprawy w zadania i odpala je jako osobnych agentów w herdr: zależności, fale, równoległy start przez koordynatora, autopilot.

- `orch.py`: CLI projektów i zadań (`python orch.py -h`). Zadania są w `<projekt>/TASKS.md` i w `tasks.json`.
- `CLAUDE.md`: instrukcje dla agenta-orkiestratora. `.claude/settings.json`: jego uprawnienia (zapis tylko przez `orch.py`).
- `test_orch.py`: test na kopii w katalogu tymczasowym, z atrapą herdr.
- `herdr/`: dodatki do herdr, czyli panel zadań z przyciskiem „Uruchom razem”, pasek kart z przyciskiem orkiestratora i layouty.

## Wymagania

- Python 3.10+ (tylko biblioteka standardowa) i git.
- [herdr](https://herdr.dev): `herdr-app` i `hc` (herdr-core) w `PATH`.
- [Claude Code](https://claude.com/claude-code) (`claude` w `PATH`), zalogowany. Opcjonalnie Codex CLI (`codex`) do zadań z grafiką.
- Opcjonalnie [gh](https://cli.github.com).

## Instalacja

```bash
git clone https://github.com/XangoAbada/orchestrator.git D:/Projects/orchestrator
cd D:/Projects/orchestrator
python test_orch.py          # OK
python orch.py scan          # rejestruje repozytoria git z katalogu projektów
python orch.py projects
```

`projects.json` i `tasks.json` nie są w repo. `orch` tworzy je przy pierwszym zapisie, a listę projektów buduje `orch scan`.

### Konfiguracja

| Zmienna | Domyślnie | Co robi |
|---|---|---|
| `ORCH_ROOT` | `D:/Projects` | katalog z projektami (każdy podfolder z `.git` to projekt) |
| `ORCH_HERDR` | `["herdr-app"]` | polecenie herdr jako lista JSON |

Przykład (PowerShell, na stałe): `setx ORCH_ROOT "E:\code"`.

## Panel w herdr

Pliki z `herdr/` kopiujesz do konfiguracji herdr (`%USERPROFILE%\.config\herdr-app\`, na macOS i Linux `~/.config/herdr-app/`). Herdr przeładowuje mody na żywo.

```bash
cp -r herdr/mods/* ~/.config/herdr-app/mods/
cp herdr/layouts/* ~/.config/herdr-app/layouts/
```

- `mods/tasks`: panel „Zadania”, tablica zadań z falami, startem, „Uruchom razem” dla grupy równoległej i zatwierdzaniem `done`. Wszystkie zmiany robi przez `orch.py`.
- `mods/tabs-orch`: pasek kart z przyciskiem orkiestratora. Uruchamia terminal `orchestrator` (Claude Code w folderze orkiestratora) i pokazuje go w panelu bocznym razem z zadaniami albo na pełnym oknie.
- `layouts/main-limits.json`: główny layout z `tabs-orch` zamiast `tabs` i z paskiem limitów na dole. `layouts/orch.json`: widok pełnookienny, czat orkiestratora obok zadań.
- `mods/limits`: pasek zużycia limitów Claude i Codex. Czyta lokalne tokeny z `~/.claude/.credentials.json` i `~/.codex/auth.json`.
- `mods/agents-ctx`: lista agentów z zajętością kontekstu (klon wbudowanego `agents`, `ctx.ps1` działa tylko na Windows).

`limits` i `agents-ctx` nie są potrzebne do samego orkiestratora, ale używa ich `main-limits.json`.

Potem przełącz layout na `main-limits` (`ctrl-shift-l` albo z palety `layout.switch`).

Jeśli orkiestrator nie leży w `D:\Projects\orchestrator`, popraw ścieżkę w dwóch stałych: `DIR` w `mods/tasks/main.js` i `ORCH_DIR` w `mods/tabs-orch/main.js`.

Pliki w `herdr/` to kopia tego, co działa w `~/.config/herdr-app`. Po zmianie moda tam skopiuj go z powrotem do repo.
