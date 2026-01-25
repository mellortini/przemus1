# Konfiguracja Przemusia - Web App z Google Login

## 1. Utwórz aplikację Google OAuth

1. Wejdź na: https://console.cloud.google.com/apis/credentials
2. Utwórz nowy projekt (lub wybierz istniejący)
3. W menu bocznym: **APIs & Services** → **OAuth consent screen**
   - User Type: **External**
   - Wypełnij nazwę aplikacji: "Przemuś"
   - Dodaj swój email jako test user
4. **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
   - Application type: **Web application**
   - Name: "Przemuś Web"
   - Authorized redirect URIs: `http://localhost:5000/auth/callback`
5. Skopiuj **Client ID** i **Client Secret**

## 2. Skonfiguruj zmienne środowiskowe

Utwórz plik `.env` w folderze `ai-os-agent/`:

```env
# Google OAuth
GOOGLE_CLIENT_ID=twoj-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=twoj-client-secret

# Secret key (wygeneruj losowy)
SECRET_KEY=wklej-losowy-32-znakowy-klucz
```

Aby wygenerować SECRET_KEY:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 3. Uruchom aplikację

```bash
cd ai-os-agent/src
python web.py
```

Otwórz: http://localhost:5000

## 4. Produkcja

Dla wdrożenia produkcyjnego:
- Zmień redirect URI na domenę produkcyjną
- Użyj HTTPS
- Ustaw silny SECRET_KEY
- Rozważ PostgreSQL zamiast SQLite
