# AI Agent - Osobisty asystent z pamięcią

Agent AI przez OpenAI API z trwałą pamięcią na dysku, inspirowany projektem [MARVIN](https://github.com/SterlingChin/marvin-template).

## Funkcje

- Czyta profil, stan, cele, projekty przed każdą rozmową
- Trzyma okno ostatnich 10 wiadomości w sesji
- Po każdej odpowiedzi generuje rolling summary (FACT/DECISION/TODO/PREF)
- Logi sesji dzienne w osobnych plikach
- System skills jako pliki markdown
- Komendy workflow: `/plan`, `/update`, `/end`

## Instalacja

```bash
cd ai-os-agent
pip install -r requirements.txt
```

## Konfiguracja

Utwórz plik `.env` w katalogu `ai-os-agent/`:

```
OPENAI_API_KEY=twoj_klucz_api
OPENAI_MODEL=gpt-4o
```

## Uruchomienie

```bash
cd src
python cli.py
```

## Komendy

| Komenda | Opis |
|---------|------|
| `/plan` | Briefing na start dnia - priorytety, cele, alerty |
| `/update` | Szybki checkpoint - zapisz postęp bez kończenia |
| `/end` | Zakończ sesję z pełnym podsumowaniem |
| `/help` | Lista wszystkich komend |
| `clear` | Wyczyść historię sesji |
| `exit` | Wyjście z programu |

## Struktura plików

```
ai-os-agent/
├── src/
│   ├── agent.py      # Logika agenta + komendy
│   ├── memory.py     # Operacje na plikach
│   ├── llm.py        # Wrapper OpenAI API
│   ├── cli.py        # Interfejs terminalowy
│   └── config.py     # Konfiguracja
├── data/
│   ├── profile.md    # Profil użytkownika
│   ├── projects.md   # Aktywne projekty
│   ├── memory.md     # Rolling log pamięci
│   ├── state/
│   │   ├── current.md  # Aktualny stan, priorytety
│   │   ├── goals.md    # Cele zawodowe i osobiste
│   │   └── todos.md    # Lista zadań
│   ├── sessions/     # Logi sesji (YYYY-MM-DD.md)
│   └── skills/       # Umiejętności jako markdown
│       ├── _template/  # Szablon nowego skilla
│       ├── planning/   # Skill planowania
│       └── review/     # Skill przeglądów
├── .env              # Klucz API (nie commitować)
└── requirements.txt
```

## Workflow

### Start dnia
```
python cli.py
/plan
```
Agent pokaże briefing z priorytetami i alertami.

### W trakcie pracy
Rozmawiaj normalnie. Użyj `/update` co jakiś czas aby zapisać postęp.

### Koniec dnia
```
/end
```
Agent stworzy podsumowanie i zapisze wszystko.

## Skills

Skills to pliki markdown w `data/skills/`, które uczą agenta nowych umiejętności.

Aby dodać nowy skill:
1. Skopiuj `data/skills/_template/`
2. Nazwij folder (np. `email/`)
3. Edytuj `SKILL.md` z instrukcjami

Agent automatycznie ładuje wszystkie skills do kontekstu.

## Kolejne kroki (roadmap)

- [ ] Rekompresja pamięci gdy > 12k znaków
- [ ] Integracja z kalendarzem
- [ ] Komenda `/report` - raport tygodniowy
- [ ] Proaktywne alerty o deadlinach
