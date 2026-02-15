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
        # Spróbuj najpierw z 1 znakiem (poprawne)
        encrypted_data = text[1:].encode()
        decrypted = f.decrypt(encrypted_data)
        return decrypted.decode()
    except Exception:
        # Fallback: może to stary format z błędnym usunięciem 2 znaków?
        try:
            key = get_user_encryption_key(user_id)
            f = Fernet(key)
            # Spróbuj z 2 znakami (stary błędny format)
            encrypted_data = text[2:].encode()
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

# Migracja pamięci (jednorazowa, przy starcie)
try:
    from migrate_memory import run_migration
    run_migration(app)
except Exception as e:
    print(f"Migration note: {e}")


# === SYSTEM PROMPT ===

SYSTEM_PROMPT = """Jesteś Przemuś - osobisty asystent AI.
Jesteś pomocny, konkretny i inteligentny.
Odpowiadasz po polsku, chyba że użytkownik pisze w innym języku.
Nie używasz emoji chyba że użytkownik ich używa."""


# === MEMORY IMPORTS ===

import memory_manager as mm
from context_packer import pack_context

# Stare funkcje pamięci usunięte — zastąpione przez memory_manager.py


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
        # Pobierz ustawienia użytkownika
        settings = current_user.settings
        provider = settings.get('provider', 'groq')
        model = settings.get('model', 'llama-3.3-70b-versatile')
        
        # Pobierz klucz API
        user_api_key = settings.get('api_keys', {}).get(provider)
        if not user_api_key:
            user_api_key = os.getenv('LLM_API_KEY')
        
        # === NOWY SYSTEM PAMIĘCI: Context Packer ===
        # Pobierz preferencje z LTM
        preferences = mm.get_preferences(current_user.id)
        
        # Pobierz working state (STM)
        ws = mm.get_working_state(conv.id, current_user.id)
        ws_str = ws.to_prompt_string() if ws.task else ""
        
        # Pobierz relevantne fakty
        relevant_facts = mm.get_relevant_facts(current_user.id, user_message)
        
        # Zbuduj minimalny kontekst z budżetem tokenów
        context = pack_context(
            system_prompt=SYSTEM_PROMPT,
            preferences=preferences,
            working_state_str=ws_str,
            relevant_facts=relevant_facts,
            conversation_messages=messages,
            user_message=user_message,
            decrypt_fn=decrypt_for_user,
            user_id=current_user.id,
            budget=3000,
            last_turns=3
        )
        
        # Wywołaj LLM
        response = ask_llm(context, provider=provider, model=model, 
                          api_key=user_api_key)
        
        # Zapisz ZASZYFROWANE wiadomości
        encrypted_user_msg = encrypt_for_user(user_message, current_user.id)
        encrypted_response = encrypt_for_user(response, current_user.id)
        
        messages.append({"role": "user", "content": encrypted_user_msg})
        messages.append({"role": "assistant", "content": encrypted_response})
        conv.messages = messages
        db.session.commit()
        
        # === NOWY SYSTEM PAMIĘCI: Process Turn ===
        try:
            result = mm.process_turn(
                user_id=current_user.id,
                conversation_id=conv.id,
                user_msg=user_message,
                assistant_msg=response,
                provider=provider,
                api_key=user_api_key
            )
            print(f"[Memory] chunk={result['chunk_id']}, candidates={result['candidates']}, committed={result['committed']}")
        except Exception as mem_err:
            print(f"[Memory] Error: {mem_err}")
        
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
        entries = mm.get_memory_entries(current_user.id)
        if not entries:
            return "Nie mam jeszcze żadnych zapisanych informacji o Tobie."
        
        result = ["**Co o Tobie pamiętam:**\n"]
        by_type = {}
        for e in entries:
            t = e['type']
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        type_names = {'fact': 'Fakty', 'pref': 'Preferencje', 'todo': 'Zadania', 
                      'decision': 'Decyzje', 'procedure': 'Procedury', 'definition': 'Definicje'}
        for t, facts in by_type.items():
            result.append(f"**{type_names.get(t, t)}:**")
            for f in facts:
                pin = "📌 " if f.get('pinned') else ""
                result.append(f"• {pin}{f['text']}")
            result.append("")
        
        return "\n".join(result)
    
    if cmd_name == '/forget':
        if not cmd_arg:
            return "Podaj co mam zapomnieć, np: /forget trading"
        count = mm.delete_facts_by_keyword(current_user.id, cmd_arg)
        if count > 0:
            return f"Zapomniałem {count} rzeczy związanych z \"{cmd_arg}\"."
        return f"Nie znalazłem nic związanego z \"{cmd_arg}\" w mojej pamięci."
    
    if cmd_name == '/forget-all':
        mm.clear_all_memory(current_user.id)
        return "Wyczyściłem całą pamięć. Zaczynamy od nowa!"
    
    return f"Nieznana komenda: {cmd}"


# === API: MEMORY ===

@app.route('/api/memory', methods=['GET'])
@login_required
def api_get_memory():
    """Pobiera wszystkie wpisy pamięci użytkownika (nowy system)."""
    entries = mm.get_memory_entries(current_user.id)
    # Format kompatybilny wstecz: lista stringów z tagami
    result = []
    for e in entries:
        pin = "📌 " if e.get('pinned') else ""
        result.append(f"[{e['type'].upper()}] {pin}{e['text']}")
    return jsonify({'entries': result, 'facts': entries})


@app.route('/api/memory', methods=['PUT'])
@login_required
def api_update_memory():
    """Aktualizuje fakt po ID."""
    data = request.json
    fact_id = data.get('id') or data.get('index')  # kompatybilność wsteczna
    new_text = data.get('text') or data.get('content', '')
    new_pinned = data.get('pinned')
    
    if fact_id is None:
        return jsonify({'error': 'Missing id'}), 400
    
    # Jeśli podano index zamiast ID, skonwertuj
    if data.get('index') is not None and data.get('id') is None:
        entries = mm.get_memory_entries(current_user.id)
        idx = int(data['index'])
        if idx < 0 or idx >= len(entries):
            return jsonify({'error': 'Invalid index'}), 400
        fact_id = entries[idx]['id']
    
    success = mm.update_fact(int(fact_id), current_user.id, 
                            new_text=new_text if new_text else None,
                            new_pinned=new_pinned)
    if success:
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/memory', methods=['DELETE'])
@login_required
def api_delete_memory():
    """Usuwa fakt po ID."""
    data = request.json
    fact_id = data.get('id') or data.get('index')  # kompatybilność wsteczna
    
    if fact_id is None:
        return jsonify({'error': 'Missing id'}), 400
    
    # Jeśli podano index zamiast ID, skonwertuj
    if data.get('index') is not None and data.get('id') is None:
        entries = mm.get_memory_entries(current_user.id)
        idx = int(data['index'])
        if idx < 0 or idx >= len(entries):
            return jsonify({'error': 'Invalid index'}), 400
        fact_id = entries[idx]['id']
    
    success = mm.delete_fact(int(fact_id), current_user.id)
    if success:
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/memory/clear', methods=['POST'])
@login_required
def api_clear_memory():
    """Czyści całą pamięć użytkownika."""
    mm.clear_all_memory(current_user.id)
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


@app.route('/api/conversations/<conv_id>/select', methods=['POST'])
@login_required
def select_conversation(conv_id):
    """Wybór rozmowy (używane przez frontend przy kliknięciu)."""
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


# === API: SEARCH ===

@app.route('/api/search', methods=['GET'])
@login_required
def search():
    """Wyszukuje w rozmowach i pamięci użytkownika."""
    query = request.args.get('q', '').strip()
    scope = request.args.get('scope', 'all')  # conversations, memory, all
    date_from = request.args.get('date_from', '')
    
    if not query:
        return jsonify({'results': []})
    
    results = []
    query_lower = query.lower()
    
    # Wyszukiwanie w rozmowach
    if scope in ['conversations', 'all']:
        conversations = Conversation.query.filter_by(user_id=current_user.id).all()
        
        for conv in conversations:
            # Sprawdź datę
            if date_from:
                from datetime import datetime as dt
                try:
                    date_obj = dt.strptime(date_from, '%Y-%m-%d').date()
                    if conv.created_at.date() < date_obj:
                        continue
                except:
                    pass
            
            messages = conv.messages
            for msg_idx, msg in enumerate(messages):
                # Odszyfruj wiadomość
                content = decrypt_for_user(msg.get('content', ''), current_user.id)
                
                if query_lower in content.lower():
                    # Znajdź kontekst (fragment z zapytaniem)
                    content_lower = content.lower()
                    idx = content_lower.find(query_lower)
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(query) + 50)
                    preview = content[start:end]
                    if start > 0:
                        preview = '...' + preview
                    if end < len(content):
                        preview = preview + '...'
                    
                    results.append({
                        'type': 'conversation',
                        'conversation_id': conv.id,
                        'message_id': msg_idx,
                        'title': decrypt_for_user(conv.title, current_user.id),
                        'preview': preview,
                        'date': conv.updated_at.isoformat(),
                        'query': query
                    })
    
    # Wyszukiwanie w pamięci
    if scope in ['memory', 'all']:
        memory_content = current_user.memory or ''
        lines = memory_content.split('\n')
        
        for line_idx, line in enumerate(lines):
            if query_lower in line.lower():
                # Sprawdź datę w linii (format: YYYY-MM-DD)
                if date_from:
                    if date_from not in line:
                        continue
                
                preview = line
                if len(preview) > 150:
                    idx = preview.lower().find(query_lower)
                    start = max(0, idx - 50)
                    end = min(len(preview), idx + len(query) + 50)
                    preview = preview[start:end]
                    if start > 0:
                        preview = '...' + preview
                    if end < len(line):
                        preview = preview + '...'
                
                results.append({
                    'type': 'memory',
                    'conversation_id': None,
                    'message_id': line_idx,
                    'title': 'Pamięć Przemusia',
                    'preview': preview,
                    'date': datetime.utcnow().isoformat(),  # Memory nie ma daty per wpis
                    'query': query
                })
    
    # Sortuj po dacie (najnowsze pierwsze)
    results.sort(key=lambda x: x['date'], reverse=True)
    
    return jsonify({'results': results})


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
