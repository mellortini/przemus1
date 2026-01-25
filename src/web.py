#!/usr/bin/env python3
"""
Przemuś - Web Application with Google OAuth
"""

import os
import uuid
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from database import db, init_db, User, Conversation
from auth import auth_bp, init_auth, oauth
from llm import ask_llm, test_connection
from config import PROVIDERS, load_settings
import json

from pathlib import Path
from werkzeug.middleware.proxy_fix import ProxyFix

# Ścieżki
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

# Tworzenie aplikacji
app = Flask(__name__, template_folder='../templates', static_folder='../static')

# Fix dla proxy (Railway, Heroku, etc.) - poprawia generowanie URL
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Konfiguracja
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATA_DIR / "przemus.db"}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PREFERRED_URL_SCHEME'] = 'https'

# Inicjalizacja
init_db(app)
init_auth(app)
app.register_blueprint(auth_bp, url_prefix='/auth')


# === SYSTEM PROMPT ===

SYSTEM_PROMPT = """Jesteś Przemuś - osobisty asystent AI.
Jesteś pomocny, konkretny i inteligentny.
Odpowiadasz po polsku, chyba że użytkownik pisze w innym języku.
Nie używasz emoji chyba że użytkownik ich używa."""


# === MEMORY FUNCTIONS ===

def get_user_memory_entries(user):
    """Parsuje pamięć użytkownika i zwraca wpisy."""
    if not user.memory:
        return []
    
    entries = []
    current_date = None
    
    for line in user.memory.split('\n'):
        line = line.strip()
        if line.startswith('### '):
            current_date = line[4:]
        elif line.startswith('['):
            bracket_end = line.find(']')
            if bracket_end > 0:
                tag = line[1:bracket_end]
                text = line[bracket_end+1:].strip()
                entries.append({
                    'id': len(entries),
                    'date': current_date,
                    'tag': tag,
                    'text': text
                })
    return entries


def delete_memory_by_keyword(user, keyword):
    """Usuwa wpisy z pamięci zawierające słowo kluczowe."""
    if not user.memory:
        return 0
    
    lines = user.memory.split('\n')
    new_lines = []
    deleted = 0
    
    for line in lines:
        if keyword.lower() in line.lower() and line.strip().startswith('['):
            deleted += 1
        else:
            new_lines.append(line)
    
    if deleted > 0:
        user.memory = '\n'.join(new_lines)
        db.session.commit()
    
    return deleted


def append_to_memory(user, entry):
    """Dodaje wpis do pamięci użytkownika."""
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Sprawdź czy mamy nagłówek dla dzisiejszego dnia
    if f'### {today}' not in user.memory:
        user.memory += f'\n### {today}\n'
    
    user.memory += entry + '\n'
    db.session.commit()


def extract_memory_from_conversation(user_msg, assistant_msg):
    """Wyciąga istotne informacje z rozmowy do pamięci."""
    prompt = f"""Przeanalizuj tę wymianę i wyciągnij TYLKO istotne, trwałe informacje o użytkowniku.
Zwróć maksymalnie 2-3 wpisy, tylko jeśli są naprawdę istotne.
Format: [TAG] informacja

Tagi:
[FACT] - fakty o użytkowniku (zawód, hobby, umiejętności)
[PREF] - preferencje użytkownika
[TODO] - zadania do zrobienia
[DECISION] - ważne decyzje

Jeśli nie ma nic istotnego, zwróć pustą odpowiedź.

Użytkownik: {user_msg}
Asystent: {assistant_msg}

Wyciągnięte informacje:"""

    try:
        result = ask_llm([
            {"role": "system", "content": "Jesteś modułem ekstrakcji pamięci. Bądź bardzo selektywny."},
            {"role": "user", "content": prompt}
        ])
        return result.strip()
    except:
        return ""


# === ROUTES ===

@app.route('/')
def index():
    """Strona główna."""
    if current_user.is_authenticated:
        return render_template('chat.html', user=current_user)
    return render_template('landing.html')


@app.route('/chat')
@login_required
def chat_page():
    """Strona chatu (wymaga logowania)."""
    return render_template('chat.html', user=current_user)


# === API: CHAT ===

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    """Wysyłanie wiadomości."""
    data = request.json
    user_message = data.get('message', '').strip()
    conversation_id = data.get('conversation_id')
    
    if not user_message:
        return jsonify({'error': 'Pusta wiadomość'}), 400
    
    # Obsługa komend
    if user_message.startswith('/'):
        response = handle_command(user_message)
        return jsonify({'response': response, 'type': 'command'})
    
    # Znajdź lub utwórz rozmowę
    if conversation_id:
        conv = Conversation.query.filter_by(id=conversation_id, user_id=current_user.id).first()
    else:
        conv = None
    
    if not conv:
        conv = Conversation(
            id=str(uuid.uuid4())[:8],
            user_id=current_user.id,
            title=user_message[:50] + '...' if len(user_message) > 50 else user_message
        )
        db.session.add(conv)
        db.session.commit()
    
    # Dodaj wiadomość użytkownika
    messages = conv.messages
    messages.append({"role": "user", "content": user_message})
    
    try:
        # Buduj kontekst
        context = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        # Dodaj pamięć użytkownika
        if current_user.memory:
            memory_summary = current_user.memory[-2000:]  # Ostatnie 2000 znaków
            context.append({"role": "system", "content": f"Pamięć o użytkowniku:\n{memory_summary}"})
        
        # Dodaj profil
        if current_user.profile:
            context.append({"role": "system", "content": f"Profil użytkownika:\n{current_user.profile}"})
        
        # Dodaj historię rozmowy (ostatnie 10 wiadomości)
        context.extend(messages[-10:])
        
        # Pobierz ustawienia użytkownika
        settings = current_user.settings
        provider = settings.get('provider', 'groq')
        model = settings.get('model', 'llama-3.3-70b-versatile')
        
        # Wywołaj LLM
        response = ask_llm(context, provider=provider, model=model, 
                          api_key=settings.get('api_keys', {}).get(provider))
        
        # Zapisz odpowiedź
        messages.append({"role": "assistant", "content": response})
        conv.messages = messages
        db.session.commit()
        
        # Wyciągnij pamięć w tle
        try:
            memory_entry = extract_memory_from_conversation(user_message, response)
            if memory_entry and memory_entry.strip():
                append_to_memory(current_user, memory_entry)
        except:
            pass
        
        return jsonify({
            'response': response,
            'type': 'message',
            'conversation_id': conv.id
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def handle_command(cmd):
    """Obsługuje komendy slash."""
    parts = cmd.strip().split(' ', 1)
    cmd_name = parts[0].lower()
    cmd_arg = parts[1] if len(parts) > 1 else ""
    
    if cmd_name == '/help':
        return """**Dostępne komendy:**

**Pamięć:**
• `/memory` - Co o Tobie pamiętam
• `/forget [słowo]` - Zapomnij rzeczy (np. /forget trading)
• `/forget-all` - Wyczyść całą pamięć

**Inne:**
• `/help` - Ta lista"""
    
    if cmd_name == '/memory':
        entries = get_user_memory_entries(current_user)
        if not entries:
            return "Nie mam jeszcze żadnych zapisanych informacji o Tobie."
        
        result = ["**Co o Tobie pamiętam:**\n"]
        by_tag = {}
        for e in entries:
            tag = e['tag']
            if tag not in by_tag:
                by_tag[tag] = []
            by_tag[tag].append(e['text'])
        
        tag_names = {'FACT': 'Fakty', 'PREF': 'Preferencje', 'TODO': 'Zadania', 'DECISION': 'Decyzje'}
        for tag, texts in by_tag.items():
            result.append(f"**{tag_names.get(tag, tag)}:**")
            for text in texts:
                result.append(f"• {text}")
            result.append("")
        
        return "\n".join(result)
    
    if cmd_name == '/forget':
        if not cmd_arg:
            return "Podaj co mam zapomnieć, np: /forget trading"
        count = delete_memory_by_keyword(current_user, cmd_arg)
        if count > 0:
            return f"Zapomniałem {count} rzeczy związanych z \"{cmd_arg}\"."
        return f"Nie znalazłem nic związanego z \"{cmd_arg}\" w mojej pamięci."
    
    if cmd_name == '/forget-all':
        current_user.memory = "# Log pamięci Przemusia\n\n"
        db.session.commit()
        return "Wyczyściłem całą pamięć. Zaczynamy od nowa!"
    
    return f"Nieznana komenda: {cmd}"


# === API: CONVERSATIONS ===

@app.route('/api/conversations', methods=['GET'])
@login_required
def get_conversations():
    """Lista rozmów użytkownika."""
    convs = Conversation.query.filter_by(user_id=current_user.id)\
        .order_by(Conversation.updated_at.desc()).all()
    
    return jsonify({
        'conversations': [
            {'id': c.id, 'title': c.title, 'updated_at': c.updated_at.isoformat()}
            for c in convs
        ]
    })


@app.route('/api/conversations', methods=['POST'])
@login_required
def new_conversation():
    """Tworzy nową rozmowę."""
    conv = Conversation(
        id=str(uuid.uuid4())[:8],
        user_id=current_user.id,
        title='Nowa rozmowa'
    )
    db.session.add(conv)
    db.session.commit()
    
    return jsonify({'conversation': conv.to_dict()})


@app.route('/api/conversations/<conv_id>', methods=['GET'])
@login_required
def get_conversation(conv_id):
    """Szczegóły rozmowy."""
    conv = Conversation.query.filter_by(id=conv_id, user_id=current_user.id).first()
    if conv:
        return jsonify({'conversation': conv.to_dict()})
    return jsonify({'error': 'Nie znaleziono'}), 404


@app.route('/api/conversations/<conv_id>', methods=['DELETE'])
@login_required
def delete_conversation(conv_id):
    """Usuwa rozmowę."""
    conv = Conversation.query.filter_by(id=conv_id, user_id=current_user.id).first()
    if conv:
        db.session.delete(conv)
        db.session.commit()
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Nie znaleziono'}), 404


# === API: SETTINGS ===

@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    """Pobiera ustawienia użytkownika."""
    return jsonify({
        'settings': current_user.settings,
        'providers': PROVIDERS
    })


@app.route('/api/settings', methods=['POST'])
@login_required
def update_settings():
    """Aktualizuje ustawienia użytkownika."""
    data = request.json
    settings = current_user.settings
    
    if 'provider' in data:
        settings['provider'] = data['provider']
    if 'model' in data:
        settings['model'] = data['model']
    if 'api_key' in data and 'provider' in data:
        if 'api_keys' not in settings:
            settings['api_keys'] = {}
        settings['api_keys'][data['provider']] = data['api_key']
    
    current_user.settings = settings
    db.session.commit()
    
    return jsonify({'status': 'ok', 'settings': settings})


@app.route('/api/test-connection', methods=['POST'])
@login_required
def test_api_connection():
    """Testuje połączenie z API."""
    data = request.json
    provider = data.get('provider')
    api_key = data.get('api_key')
    
    if not provider or not api_key:
        return jsonify({'success': False, 'message': 'Brak providera lub klucza'}), 400
    
    success, message = test_connection(provider, api_key)
    return jsonify({'success': success, 'message': message})


# === API: USER ===

@app.route('/api/user', methods=['GET'])
def get_user():
    """Pobiera dane zalogowanego użytkownika."""
    if current_user.is_authenticated:
        return jsonify({
            'logged_in': True,
            'id': current_user.id,
            'name': current_user.name,
            'email': current_user.email,
            'avatar': current_user.avatar_url
        })
    return jsonify({'logged_in': False})


# === RUN ===

def run_server(port=5000, debug=False):
    """Uruchamia serwer."""
    app.run(debug=debug, port=port, threaded=True)


if __name__ == '__main__':
    import os
    debug_mode = os.getenv('FLASK_DEBUG', 'true').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    
    print("\n" + "=" * 50)
    print("  PRZEMUŚ - Web Application")
    print(f"  Otwórz: http://localhost:{port}")
    print("=" * 50 + "\n")
    run_server(debug=debug_mode, port=port)
