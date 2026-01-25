#!/usr/bin/env python3
"""
Przemuś - Web Application with Google OAuth
"""

import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from database import db, init_db, User, Conversation, Feedback
from auth import auth_bp, init_auth, oauth
from llm import ask_llm, test_connection
from config import PROVIDERS, load_settings
import json

from pathlib import Path
from werkzeug.middleware.proxy_fix import ProxyFix
import hashlib
from cryptography.fernet import Fernet
import base64

# === SZYFROWANIE ROZMÓW ===
# Każdy user ma unikalny klucz szyfrowania oparty na jego ID + sekretna sól
# Admin nie może odszyfrować bez znajomości soli i pisania kodu

def get_user_encryption_key(user_id: int) -> bytes:
    """Generuje klucz szyfrowania dla usera."""
    # Sól z zmiennej środowiskowej (lub domyślna dla dev)
    salt = os.getenv('ENCRYPTION_SALT', 'przemus-dev-salt-change-in-production')
    # Kombinacja: user_id + sól
    key_source = f"{user_id}-{salt}-przemus-private"
    # Generuj 32-bajtowy klucz (Fernet wymaga)
    key_hash = hashlib.sha256(key_source.encode()).digest()
    return base64.urlsafe_b64encode(key_hash)

def encrypt_for_user(text: str, user_id: int) -> str:
    """Szyfruje tekst dla konkretnego usera."""
    if not text:
        return text
    try:
        key = get_user_encryption_key(user_id)
        f = Fernet(key)
        encrypted = f.encrypt(text.encode())
        return "🔒" + encrypted.decode()  # Prefix żeby rozpoznać zaszyfrowane
    except Exception as e:
        print(f"Encryption error: {e}")
        return text

def decrypt_for_user(text: str, user_id: int) -> str:
    """Odszyfrowuje tekst dla konkretnego usera."""
    if not text or not text.startswith("🔒"):
        return text  # Nie zaszyfrowane
    try:
        key = get_user_encryption_key(user_id)
        f = Fernet(key)
        encrypted_data = text[2:].encode()  # Usuń prefix 🔒
        decrypted = f.decrypt(encrypted_data)
        return decrypted.decode()
    except Exception as e:
        print(f"Decryption error: {e}")
        return "[nie można odszyfrować]"

# Ścieżki
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

# Tworzenie aplikacji
app = Flask(__name__, template_folder='../templates', static_folder='../static')

# Fix dla proxy (Railway, Heroku, etc.) - poprawia generowanie URL
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Konfiguracja bazy danych
# Na produkcji używa PostgreSQL (DATABASE_URL), lokalnie SQLite
import os
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL:
    # Railway PostgreSQL - zamień postgres:// na postgresql:// (SQLAlchemy wymaga)
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Lokalnie - SQLite
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
    """Dodaje wpis do pamięci użytkownika (bez duplikatów)."""
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    if not entry or not entry.strip():
        return
    
    # Wyciągnij istniejące fakty z pamięci (bez dat)
    existing_facts = set()
    for line in user.memory.split('\n'):
        line = line.strip()
        if line.startswith('['):
            # Normalizuj: usuń tagi i sprowadź do małych liter
            clean = line.lower()
            for tag in ['[fact]', '[pref]', '[todo]', '[decision]']:
                clean = clean.replace(tag, '')
            existing_facts.add(clean.strip())
    
    # Filtruj nowe wpisy - dodaj tylko te których jeszcze nie ma
    new_entries = []
    for line in entry.split('\n'):
        line = line.strip()
        if not line or not line.startswith('['):
            continue
        
        clean = line.lower()
        for tag in ['[fact]', '[pref]', '[todo]', '[decision]']:
            clean = clean.replace(tag, '')
        clean = clean.strip()
        
        # Sprawdź czy podobny fakt już istnieje
        is_duplicate = False
        for existing in existing_facts:
            # Prosta heurystyka: jeśli >70% słów się pokrywa, to duplikat
            new_words = set(clean.split())
            exist_words = set(existing.split())
            if len(new_words) > 0 and len(exist_words) > 0:
                overlap = len(new_words & exist_words) / max(len(new_words), len(exist_words))
                if overlap > 0.7:
                    is_duplicate = True
                    break
        
        if not is_duplicate:
            new_entries.append(line)
            existing_facts.add(clean)
    
    # Dodaj tylko nowe, unikalne wpisy
    if new_entries:
        if f'### {today}' not in user.memory:
            user.memory += f'\n### {today}\n'
        
        user.memory += '\n'.join(new_entries) + '\n'
        db.session.commit()


def extract_memory_from_conversation(user_msg, assistant_msg, existing_memory='', provider='groq', api_key=None):
    """Wyciąga istotne informacje z rozmowy do pamięci (bez duplikatów)."""
    
    # Pokaż LLM co już wie, żeby nie powtarzał
    memory_context = ""
    if existing_memory and existing_memory.strip():
        # Wyciągnij tylko wpisy [TAG], bez dat
        existing_facts = []
        for line in existing_memory.split('\n'):
            line = line.strip()
            if line.startswith('['):
                existing_facts.append(line)
        if existing_facts:
            memory_context = f"""
CO JUŻ WIEM O UŻYTKOWNIKU (NIE POWTARZAJ TEGO):
{chr(10).join(existing_facts[-20:])}

"""
    
    prompt = f"""{memory_context}Przeanalizuj tę wymianę i wyciągnij TYLKO NOWE, istotne informacje o użytkowniku.

WAŻNE:
- NIE powtarzaj informacji które już znam (pokazane wyżej)
- Jeśli informacja jest semantycznie taka sama (np. "ma na imię X" vs "nazywa się X"), NIE dodawaj
- Zwróć TYLKO naprawdę NOWE fakty
- Jeśli nie ma nic nowego, zwróć pustą odpowiedź

Format: [TAG] informacja
Tagi: [FACT], [PREF], [TODO], [DECISION]

Użytkownik: {user_msg}
Asystent: {assistant_msg}

Nowe informacje (lub pusta odpowiedź):"""

    try:
        if not api_key:
            api_key = os.getenv('LLM_API_KEY')
        
        result = ask_llm([
            {"role": "system", "content": "Jesteś modułem ekstrakcji pamięci. Wyciągaj TYLKO nowe informacje, których jeszcze nie znasz. Bądź BARDZO selektywny."},
            {"role": "user", "content": prompt}
        ], provider=provider, api_key=api_key)
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
        # Szyfruj tytuł (pierwsze 50 znaków wiadomości)
        title_text = user_message[:50] + '...' if len(user_message) > 50 else user_message
        encrypted_title = encrypt_for_user(title_text, current_user.id)
        
        conv = Conversation(
            id=str(uuid.uuid4())[:8],
            user_id=current_user.id,
            title=encrypted_title
        )
        db.session.add(conv)
        db.session.commit()
    
    # Pobierz historię rozmowy
    messages = conv.messages
    
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
        
        # Dodaj historię rozmowy (odszyfrowaną dla LLM) - BEZ aktualnej wiadomości
        for msg in messages[-9:]:  # Ostatnie 9 (zostawiamy miejsce na aktualną)
            decrypted_content = decrypt_for_user(msg.get('content', ''), current_user.id)
            context.append({"role": msg['role'], "content": decrypted_content})
        
        # Dodaj AKTUALNĄ wiadomość użytkownika (plaintext - jeszcze nie zaszyfrowana)
        context.append({"role": "user", "content": user_message})
        
        # Pobierz ustawienia użytkownika
        settings = current_user.settings
        provider = settings.get('provider', 'groq')
        model = settings.get('model', 'llama-3.3-70b-versatile')
        
        # Pobierz klucz API - najpierw z ustawień użytkownika, potem z env
        user_api_key = settings.get('api_keys', {}).get(provider)
        if not user_api_key:
            # Fallback do zmiennych środowiskowych (klucze admina)
            # Używamy LLM_API_KEY jako uniwersalny klucz
            user_api_key = os.getenv('LLM_API_KEY')
        
        # Wywołaj LLM
        response = ask_llm(context, provider=provider, model=model, 
                          api_key=user_api_key)
        
        # Teraz zapisz ZASZYFROWANE wiadomości (user + assistant)
        encrypted_user_msg = encrypt_for_user(user_message, current_user.id)
        encrypted_response = encrypt_for_user(response, current_user.id)
        
        messages.append({"role": "user", "content": encrypted_user_msg})
        messages.append({"role": "assistant", "content": encrypted_response})
        conv.messages = messages
        db.session.commit()
        
        # Wyciągnij pamięć w tle (przekaż istniejącą pamięć żeby nie było duplikatów)
        try:
            memory_entry = extract_memory_from_conversation(
                user_message, response, 
                existing_memory=current_user.memory or '',
                provider=provider, api_key=user_api_key
            )
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


# === API: MEMORY ===

@app.route('/api/memory', methods=['GET'])
@login_required
def api_get_memory():
    """Pobiera wszystkie wpisy pamięci użytkownika."""
    entries = get_user_memory_entries(current_user)
    # Zwróć jako lista stringów z tagami
    result = []
    for e in entries:
        result.append(f"[{e['tag']}] {e['text']}")
    return jsonify({'entries': result})


@app.route('/api/memory', methods=['PUT'])
@login_required
def api_update_memory():
    """Aktualizuje wpis pamięci po indeksie."""
    data = request.json
    idx = data.get('index')
    new_content = data.get('content', '')
    
    entries = get_user_memory_entries(current_user)
    if idx < 0 or idx >= len(entries):
        return jsonify({'error': 'Invalid index'}), 400
    
    # Odbuduj pamięć z aktualizacją
    lines = current_user.memory.split('\n')
    new_lines = []
    entry_idx = 0
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and any(stripped.startswith(f'[{t}]') for t in ['FACT', 'PREF', 'TODO', 'DECISION']):
            if entry_idx == idx:
                new_lines.append(new_content)
            else:
                new_lines.append(line)
            entry_idx += 1
        else:
            new_lines.append(line)
    
    current_user.memory = '\n'.join(new_lines)
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/memory', methods=['DELETE'])
@login_required
def api_delete_memory():
    """Usuwa wpis pamięci po indeksie."""
    data = request.json
    idx = data.get('index')
    
    entries = get_user_memory_entries(current_user)
    if idx < 0 or idx >= len(entries):
        return jsonify({'error': 'Invalid index'}), 400
    
    # Odbuduj pamięć bez usuniętego wpisu
    lines = current_user.memory.split('\n')
    new_lines = []
    entry_idx = 0
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and any(stripped.startswith(f'[{t}]') for t in ['FACT', 'PREF', 'TODO', 'DECISION']):
            if entry_idx != idx:
                new_lines.append(line)
            entry_idx += 1
        else:
            new_lines.append(line)
    
    current_user.memory = '\n'.join(new_lines)
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/memory/clear', methods=['POST'])
@login_required
def api_clear_memory():
    """Czyści całą pamięć użytkownika."""
    current_user.memory = "# Log pamięci Przemusia\n\n"
    db.session.commit()
    return jsonify({'status': 'ok'})


# === API: CONVERSATIONS ===

@app.route('/api/conversations', methods=['GET'])
@login_required
def get_conversations():
    """Lista rozmów użytkownika (z odszyfrowanymi tytułami)."""
    convs = Conversation.query.filter_by(user_id=current_user.id)\
        .order_by(Conversation.updated_at.desc()).all()
    
    return jsonify({
        'conversations': [
            {
                'id': c.id, 
                'title': decrypt_for_user(c.title, current_user.id),  # Odszyfruj dla usera
                'updated_at': c.updated_at.isoformat()
            }
            for c in convs
        ]
    })


@app.route('/api/conversations', methods=['POST'])
@login_required
def new_conversation():
    """Tworzy nową rozmowę."""
    encrypted_title = encrypt_for_user('Nowa rozmowa', current_user.id)
    conv = Conversation(
        id=str(uuid.uuid4())[:8],
        user_id=current_user.id,
        title=encrypted_title
    )
    db.session.add(conv)
    db.session.commit()
    
    # Zwróć z odszyfrowanym tytułem dla usera
    return jsonify({
        'conversation': {
            'id': conv.id,
            'title': 'Nowa rozmowa',  # Wiemy co to jest, nie trzeba odszyfrowywać
            'messages': [],
            'created_at': conv.created_at.isoformat(),
            'updated_at': conv.updated_at.isoformat()
        }
    })


@app.route('/api/conversations/<conv_id>', methods=['GET'])
@login_required
def get_conversation(conv_id):
    """Szczegóły rozmowy (z odszyfrowanymi wiadomościami)."""
    conv = Conversation.query.filter_by(id=conv_id, user_id=current_user.id).first()
    if conv:
        # Odszyfruj wiadomości dla usera
        decrypted_messages = []
        for msg in conv.messages:
            decrypted_messages.append({
                'role': msg['role'],
                'content': decrypt_for_user(msg.get('content', ''), current_user.id)
            })
        
        return jsonify({
            'conversation': {
                'id': conv.id,
                'title': decrypt_for_user(conv.title, current_user.id),  # Odszyfruj tytuł
                'messages': decrypted_messages,
                'created_at': conv.created_at.isoformat(),
                'updated_at': conv.updated_at.isoformat()
            }
        })
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
        # Sprawdź czy user jest adminem
        admin_email = os.getenv('ADMIN_EMAIL', 'mateuszmalekto@gmail.com')
        is_admin = current_user.email.lower() == admin_email.lower()
        
        return jsonify({
            'logged_in': True,
            'id': current_user.id,
            'name': current_user.name,
            'email': current_user.email,
            'avatar': current_user.avatar_url,
            'is_admin': is_admin
        })
    return jsonify({'logged_in': False})


# === API: FEEDBACK ===

@app.route('/api/feedback', methods=['POST'])
@login_required
def submit_feedback():
    """Zapisuje feedback od użytkownika."""
    data = request.json
    feedback_type = data.get('type', 'other')
    content = data.get('content', '').strip()
    
    if not content:
        return jsonify({'error': 'Pusta wiadomość'}), 400
    
    feedback = Feedback(
        user_id=current_user.id,
        user_email=current_user.email,
        type=feedback_type,
        content=content,
        status='new'
    )
    db.session.add(feedback)
    db.session.commit()
    
    return jsonify({'status': 'ok', 'message': 'Feedback zapisany'})


@app.route('/api/feedback/admin', methods=['GET'])
@login_required
def get_feedback_admin():
    """Pobiera wszystkie feedbacki (tylko dla admina)."""
    admin_email = os.getenv('ADMIN_EMAIL', 'mateuszmalekto@gmail.com')
    if current_user.email.lower() != admin_email.lower():
        return jsonify({'error': 'Brak uprawnień'}), 403
    
    feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).all()
    return jsonify({
        'feedbacks': [fb.to_dict() for fb in feedbacks]
    })


@app.route('/api/feedback/<int:feedback_id>/read', methods=['POST'])
@login_required
def mark_feedback_read(feedback_id):
    """Oznacza feedback jako przeczytany (tylko dla admina)."""
    admin_email = os.getenv('ADMIN_EMAIL', 'mateuszmalekto@gmail.com')
    if current_user.email.lower() != admin_email.lower():
        return jsonify({'error': 'Brak uprawnień'}), 403
    
    feedback = Feedback.query.get_or_404(feedback_id)
    feedback.status = 'read'
    feedback.read_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'status': 'ok'})


@app.route('/api/feedback/<int:feedback_id>/resolved', methods=['POST'])
@login_required
def mark_feedback_resolved(feedback_id):
    """Oznacza feedback jako rozwiązany (tylko dla admina)."""
    admin_email = os.getenv('ADMIN_EMAIL', 'mateuszmalekto@gmail.com')
    if current_user.email.lower() != admin_email.lower():
        return jsonify({'error': 'Brak uprawnień'}), 403
    
    feedback = Feedback.query.get_or_404(feedback_id)
    feedback.status = 'resolved'
    db.session.commit()
    
    return jsonify({'status': 'ok'})


@app.route('/api/feedback/<int:feedback_id>', methods=['DELETE'])
@login_required
def delete_feedback(feedback_id):
    """Usuwa feedback (tylko dla admina)."""
    admin_email = os.getenv('ADMIN_EMAIL', 'mateuszmalekto@gmail.com')
    if current_user.email.lower() != admin_email.lower():
        return jsonify({'error': 'Brak uprawnień'}), 403
    
    feedback = Feedback.query.get_or_404(feedback_id)
    db.session.delete(feedback)
    db.session.commit()
    
    return jsonify({'status': 'ok'})


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
