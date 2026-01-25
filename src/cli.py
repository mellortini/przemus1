#!/usr/bin/env python3
"""
AI Agent CLI - Interfejs terminalowy do rozmowy z agentem.

Komendy:
    /plan   - Briefing na start dnia
    /update - Szybki checkpoint
    /end    - Zakończ sesję z podsumowaniem
    /help   - Lista komend
    exit    - Wyjście
    clear   - Wyczyść sesję
"""

from agent import (
    build_context, update_memory,
    cmd_plan, cmd_update, cmd_end,
    COMMANDS, get_commands_help
)
from llm import ask_llm
from memory import ensure_files, append_to_session


def print_banner():
    """Wyświetla baner powitalny."""
    print("\n" + "=" * 55)
    print("   AI AGENT - Twój osobisty asystent operacyjny")
    print("=" * 55)
    print("  /plan - briefing  |  /update - checkpoint  |  /end - koniec")
    print("  /help - komendy   |  exit - wyjście        |  clear - reset")
    print("-" * 55 + "\n")


def handle_command(cmd: str, session: list[dict]) -> tuple[str, bool]:
    """
    Obsługuje komendy slash.
    Zwraca (odpowiedź, czy_kontynuować).
    """
    cmd_lower = cmd.lower().strip()
    
    if cmd_lower == "/help":
        return get_commands_help(), True
    
    if cmd_lower == "/plan":
        try:
            result = cmd_plan()
            append_to_session("### /plan\nWykonano briefing dnia.")
            return result, True
        except Exception as e:
            return f"Błąd: {e}", True
    
    if cmd_lower == "/update":
        try:
            result = cmd_update(session)
            return result, True
        except Exception as e:
            return f"Błąd: {e}", True
    
    if cmd_lower == "/end":
        try:
            result = cmd_end(session)
            return result, False  # Kończymy sesję
        except Exception as e:
            return f"Błąd: {e}", True
    
    return None, True  # Nie rozpoznano jako komenda


def main():
    """Główna pętla CLI."""
    # Upewnij się, że pliki danych istnieją
    ensure_files()
    
    # Historia bieżącej sesji
    session: list[dict] = []
    
    print_banner()

    while True:
        try:
            user_input = input("Ty: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nDo zobaczenia!")
            break

        # Puste wejście
        if not user_input:
            continue
        
        # Komendy wyjścia
        if user_input.lower() in ("exit", "quit", "q"):
            # Jeśli była rozmowa, zaproponuj /end
            if len(session) >= 2:
                print("\nMasz niezapisaną rozmowę. Użyj /end aby zapisać lub wpisz 'exit' ponownie.")
                confirm = input("Ty: ").strip().lower()
                if confirm in ("exit", "quit", "q"):
                    print("\nDo zobaczenia!")
                    break
                elif confirm == "/end":
                    result, _ = handle_command("/end", session)
                    print(f"\n{result}\n")
                    break
                continue
            print("\nDo zobaczenia!")
            break
        
        # Clear sesji
        if user_input.lower() == "clear":
            session.clear()
            print("[Sesja wyczyszczona]\n")
            continue

        # Obsługa komend slash
        if user_input.startswith("/"):
            result, should_continue = handle_command(user_input, session)
            if result:
                print(f"\n{result}\n")
            if not should_continue:
                break
            continue

        # Normalna rozmowa - dodaj do sesji
        session.append({"role": "user", "content": user_input})
        
        # Zbuduj kontekst i wywołaj LLM
        context = build_context(session)
        
        try:
            response = ask_llm(context)
        except Exception as e:
            print(f"\n[Błąd API]: {e}\n")
            session.pop()  # Usuń wiadomość z sesji jeśli błąd
            continue

        # Wyświetl odpowiedź
        print(f"\nAgent: {response}\n")
        
        # Dodaj odpowiedź do sesji
        session.append({"role": "assistant", "content": response})

        # Aktualizuj pamięć (rolling summary)
        try:
            update_memory(user_input, response)
        except Exception as e:
            pass  # Cicha porażka - nie przerywaj rozmowy


if __name__ == "__main__":
    main()
