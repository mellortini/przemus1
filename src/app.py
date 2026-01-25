#!/usr/bin/env python3
"""
AI Agent - Standalone Desktop Application
Uruchamia interfejs webowy w natywnym oknie.
"""

import webview
import threading
import sys
from pathlib import Path

# Dodaj src do path
sys.path.insert(0, str(Path(__file__).parent))

from web import app, ensure_files
from config import load_settings, PROVIDERS


class API:
    """API dostępne z JavaScript przez pywebview."""
    
    def get_settings(self):
        """Pobiera ustawienia."""
        from config import load_settings, get_current_provider, get_current_model
        settings = load_settings()
        return {
            'settings': settings,
            'providers': PROVIDERS,
            'current_provider': get_current_provider(),
            'current_model': get_current_model()
        }


def start_server():
    """Uruchamia serwer Flask w osobnym wątku."""
    app.run(port=5000, debug=False, use_reloader=False, threaded=True)


def main():
    """Główna funkcja uruchamiająca aplikację."""
    print("\n" + "=" * 50)
    print("  PRZEMUŚ - Desktop Application")
    print("=" * 50 + "\n")
    
    # Upewnij się, że pliki istnieją
    ensure_files()
    
    # Uruchom serwer Flask w tle
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    # Poczekaj chwilę na start serwera
    import time
    time.sleep(1)
    
    # Utwórz okno aplikacji
    window = webview.create_window(
        title='Przemuś',
        url='http://127.0.0.1:5000',
        width=1200,
        height=800,
        min_size=(800, 600),
        resizable=True,
        text_select=True,
        zoomable=True,
    )
    
    # Uruchom aplikację
    webview.start(debug=False)


if __name__ == '__main__':
    main()
