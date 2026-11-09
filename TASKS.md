# Zadania: orchestrator

<!-- Zarządzane przez orkiestratora (orch.py). Można edytować ręcznie w tym formacie.
     Nowe zadanie: nagłówek "## T-? · tytuł", ID zostanie nadane przy następnym odczycie. -->

## T-96 · Zrób z orkiestratora repozytorium na GitHubie
- status: running
- agent: claude
- created: 2026-11-09

Cel: orkiestrator z D:\Projects\orchestrator ląduje w repozytorium na GitHubie, tak żeby dało się go sklonować i uruchomić na innym komputerze.
Kontekst: folder nie jest dziś repozytorium git. Pliki: orch.py, test_orch.py, CLAUDE.md, TASKS.md, .claude/settings.json oraz dane tej maszyny: projects.json i tasks.json. Katalog projektów można już zmienić przez ORCH_ROOT (domyślnie D:/Projects), a polecenie herdr przez ORCH_HERDR.
Decyzje użytkownika:
- Repo publiczne. Przed pushem sprawdź, że nic prywatnego (klucze, e-maile, ścieżki z danymi osobistymi) nie trafia do repo.
- projects.json i tasks.json pomijamy (.gitignore). Na nowym komputerze listę projektów buduje `orch scan`, więc orch musi ruszyć bez tych plików.
- Do repo trafia też panel z przyciskiem „Uruchom razem” i inne dodatki orkiestratora spoza tego folderu (np. mody/pluginy Claude Code). Znajdź, gdzie leżą, i opisz w README, jak je zainstalować na nowym komputerze.
Zakres: git init, .gitignore, pierwszy commit, utworzenie publicznego repo na GitHubie (gh) i push. README: wymagania (Python, herdr, Claude Code), instalacja i konfiguracja na nowym komputerze.
Pytanie otwarte: nazwa repo (domyślnie „orchestrator”, jeśli użytkownik nie poda innej).
Gotowe, gdy: repo jest publiczne na GitHubie, świeży klon w innym folderze, bez plików json, przechodzi testy i `orch scan`, a README opisuje uruchomienie na nowym komputerze razem z panelem.

## T-85 · Napraw ucinanie pierwszej wiadomości agenta na cudzysłowie
- status: done
- agent: claude
- created: 2026-11-09

Cel: agent startowany przez `orch.py start` (zwykły i koordynator) dostaje pełną wiadomość: brief i instrukcję zgłoszenia.

Kontekst: herdr przekazuje `task` z `tab.new`/`space.new` agentowi jako argument wiersza poleceń. Na Windows obejmuje go cudzysłowami bez escapowania prostego cudzysłowu (U+0022) w środku. Każdy taki znak w treści przełącza więc cytowanie, a pierwsza spacja poza cytowaniem kończy wiadomość. Dowód w transkryptach (~/.claude/projects/*/…jsonl, pierwsza wiadomość użytkownika): wszystkie zadania urywają się na `--note <jedno` (z `brief_message`, orch.py ~l.530). Koordynator T-8/T-9 (ModKreator, sesja c2b47327…) stracił przez to całe briefy, bo idą po punkcie 4 w `coordinator_message` (~l.622). Polskie znaki docierają poprawnie. Ten brief celowo nie zawiera znaku U+0022.

Zakres: w `orch.py` przed przekazaniem `task` do herdr (`cmd_start`, `start_coordinator`) zamień U+0022 na apostrof. Dotyczy to też briefów od użytkownika. Wystarczy jedno miejsce, przez które przechodzą oba starty, z komentarzem `ponytail:`, że to obejście błędu herdr. Test w `test_orch.py`: wiadomość z U+0022 w briefie dociera do atrapy herdr (`ORCH_HERDR`) bez tego znaku.

Gotowe, gdy: testy `test_orch.py` przechodzą z nowym testem, a zadanie startowane na próbę dostaje wiadomość do końca, łącznie z „Nie edytuj ręcznie TASKS.md.” i sekcjami briefów u koordynatora.

Pytanie: zgłosić błąd do herdr (`herdr-app feedback`)? Dopiero po zgodzie użytkownika.

## T-86 · Wybieraj space herdr po nazwie projektu, nie po folderze
- status: done
- agent: claude
- created: 2026-11-09

Cel: zadanie startowane przez orch.py (zwykłe i koordynator) ląduje w space herdr nazwanym jak projekt.

Kontekst: T-14 i T-15 (magnatV2) wystartowały w poprawnym folderze, ale w space o nazwie inshootsanity. Przyczyna: `find_space` (orch.py ~l.263) szuka space po nazwie projektu, a gdy go nie znajdzie, bierze pierwszy space z tym samym folderem. W herdr nazwy space nie odpowiadają folderom (herdr-app ls z 9.11.2026): space inshootsanity ma folder D:\Projects\magnatV2, bowriV2 ma inshootsanity, bowri-landing ma bowriV2, roman-landing ma bowri-landing, NowhereAtlas ma bowriV2. Space magnatV2 nie istniał, więc dopasowanie po folderze wybrało cudzy space.

Zakres: dopasowanie space tylko po nazwie projektu. Gdy takiego space nie ma, `space.new` z nazwą projektu (ta gałąź już istnieje w `cmd_start` i `start_coordinator`). Jeśli dopasowanie po folderze ma zostać jako zapas, to wyłącznie dla space, którego nazwa nie jest nazwą innego zarejestrowanego projektu. Wybierz prostsze rozwiązanie i uzasadnij je w jednym zdaniu. Test w `test_orch.py` z atrapą herdr (`ORCH_HERDR`): space o innej nazwie i tym samym folderze nie zostaje wybrany.

Gotowe, gdy: testy `test_orch.py` przechodzą z nowym testem, a start zadania w projekcie bez własnego space tworzy space nazwany jak projekt.

Uwaga: T-85 też zmienia orch.py (cudzysłowy w wiadomości). Zmiany są rozłączne, ale nie uruchamiaj obu naraz w tym samym checkoucie bez koordynatora.

## T-90 · Sprawdzaj niezakomitowane zmiany przed zamknięciem zadania
- status: done
- agent: claude
- created: 2026-11-09

Cel: gdy użytkownik przestawia zadanie z review na done, najpierw sprawdzić, czy wszystkie zmiany są zakomitowane, a dopiero potem zamknąć okno zadania (terminal/tab w herdr).
Zachowanie (od użytkownika):
- Zmiany zakomitowane: zamknij okno zadania.
- Są niezakomitowane zmiany, zadanie ma worktree: zapytaj użytkownika, czy najpierw zrobić commit i scalić do main, czy zostawić zmiany na worktree.
- Są niezakomitowane zmiany, bez worktree: zapytaj tylko, czy zrobić commit.
Kontekst: orch.py (cmd_edit, przejście na done koło `finished = ...`) nie zamyka dziś żadnego okna; pola terminal/tab/space/worktree są w tasks.json. Sprawdzanie np. `git -C <worktree albo ścieżka projektu> status --short`. orch.py działa nieinteraktywnie, więc pytanie zadaje orkiestrator w rozmowie (albo panel); trzeba to też opisać w CLAUDE.md, w sekcji o done.
Pytania otwarte:
- Gdzie dziś zamyka się okno przy done (panel, ręcznie)? Czy zamykanie ma robić orch.py?
- Jak orch ma sygnalizować niezakomitowane zmiany: osobny kod wyjścia jak 2/3 i flagi typu --commit / --keep? Czy zadanie zostaje w review, dopóki użytkownik nie odpowie?
- Commit przy worktree: kto pisze wiadomość i scala (agent zadania w swoim terminalu czy orch)? Co z gałęzią i worktree po scaleniu?
- Autopilot: czy rusza dopiero po rozstrzygnięciu commita?
- Zadania koordynatora (wspólny terminal): zamykać okno dopiero po done ostatniego z nich?
Gotowe, gdy: done na zadaniu z czystym drzewem zamyka jego okno; przy brudnym drzewie użytkownik dostaje właściwe pytanie (z worktree albo bez) i okno zostaje otwarte do decyzji; test w test_orch.py obejmuje oba przypadki.
