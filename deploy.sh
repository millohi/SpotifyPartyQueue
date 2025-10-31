#!/usr/bin/env bash
set -euo pipefail

# --- Helper -------------------------------------------------------------------
ask() { # $1=prompt, $2=default(optional)
  local prompt="$1"
  local default="${2:-}"
  local answer
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " answer || true
    echo "${answer:-$default}"
  else
    read -r -p "$prompt: " answer || true
    echo "$answer"
  fi
}

ask_secret() { # $1=prompt
  local prompt="$1"
  local answer
  read -r -s -p "$prompt (wird nicht angezeigt): " answer
  printf '\n' >&2                 # nur fürs Terminal
  # Zeilenumbrüche/CR entfernen, nur den Wert ausgeben
  answer="${answer//$'\r'/}"
  answer="${answer//$'\n'/}"
  printf '%s' "$answer"
}


require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Fehlt: $1"; exit 1; }; }

# --- Checks -------------------------------------------------------------------
require_cmd docker
require_cmd bash

# --- Paths --------------------------------------------------------------------
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${ROOT_DIR}/env"
SQLITE_DIR="${ROOT_DIR}/sqlite"
RP_DIR="${ROOT_DIR}/reverse-proxy"
ENV_FILE="${ENV_DIR}/.env"        # Spotify etc.
DOTENV_COMPOSE="${ROOT_DIR}/.env" # nur DOMAIN/FLAGS für compose
CADDYFILE="${RP_DIR}/Caddyfile"

mkdir -p "${ENV_DIR}" "${SQLITE_DIR}" "${RP_DIR}"

# --- Mode wählen --------------------------------------------------------------
echo "== Deployment-Setup =="
echo "Möchtest du über das Internet mit echter Domain & Auto-SSL deployen?"
echo "  1) HTTPS + Domain (Caddy + Let's Encrypt)"
echo "  2) HTTP only (nur Port 80, z. B. im LAN / ohne Domain)"
MODE="$(ask "Auswahl eingeben (1/2)" "1")"

DOMAIN=""
if [[ "${MODE}" == "1" ]]; then
  DOMAIN="$(ask "Gib die Domain ein (z. B. queue.example.com)")"
  if [[ -z "${DOMAIN}" ]]; then
    echo "Domain ist leer—Abbruch."
    exit 1
  fi
fi

# --- Spotify Secrets abfragen -------------------------------------------------
echo
echo "== Spotify API Zugangsdaten =="
SPOTIFY_CLIENT_ID="$(ask "SPOTIFY_CLIENT_ID")"
SPOTIFY_CLIENT_SECRET="$(ask_secret "SPOTIFY_CLIENT_SECRET")"
SPOTIFY_REFRESH_TOKEN="$(ask_secret "SPOTIFY_REFRESH_TOKEN")"
SPOTIFY_ACCESS_TOKEN="$(ask "SPOTIFY_ACCESS_TOKEN (leer lassen, wenn unbekannt)" "")"

# --- DB-Pfad optional anpassen ------------------------------------------------
DB_FILE_DEFAULT="/app/sqlite/spotify_party_queue.sqlite3"
DB_FILE="$(ask "DB-Dateipfad im Container" "${DB_FILE_DEFAULT}")"

# --- env/.env schreiben (nur Backend-ENV) ------------------------------------
echo
echo "== Schreibe ${ENV_FILE}"
cat > "${ENV_FILE}.tmp" <<EOF
SPOTIFY_CLIENT_ID=${SPOTIFY_CLIENT_ID}
SPOTIFY_CLIENT_SECRET=${SPOTIFY_CLIENT_SECRET}
SPOTIFY_REFRESH_TOKEN=${SPOTIFY_REFRESH_TOKEN}
SPOTIFY_ACCESS_TOKEN=${SPOTIFY_ACCESS_TOKEN}
APP_ENV=production
DB_FILE=${DB_FILE}
EOF
# nur ersetzen, wenn inhaltlich anders
if [[ -f "${ENV_FILE}" ]] && cmp -s "${ENV_FILE}.tmp" "${ENV_FILE}"; then
  rm -f "${ENV_FILE}.tmp"
  echo "env/.env unverändert."
else
  mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "env/.env aktualisiert."
fi

# --- ./.env (Compose-Env für DOMAIN) -----------------------------------------
echo
echo "== Schreibe ${DOTENV_COMPOSE}"
if [[ "${MODE}" == "1" ]]; then
  echo "DOMAIN=${DOMAIN}" > "${DOTENV_COMPOSE}"
else
  echo "DOMAIN=" > "${DOTENV_COMPOSE}"
fi

# --- old_Caddyfile erzeugen -------------------------------------------------------
echo
echo "== Schreibe ${CADDYFILE}"
if [[ "${MODE}" == "1" ]]; then
  # HTTPS & Domain
  cat > "${CADDYFILE}" <<'CADDY'
{$DOMAIN} {
    encode gzip

    # Frontend (statisch)
    reverse_proxy frontend:80

    # Backend unter /api
    handle_path /api* {
        reverse_proxy backend:8000
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer-when-downgrade"
    }
}

www.{$DOMAIN} {
    redir https://{$DOMAIN}{uri}
}
CADDY
else
  # HTTP only
  cat > "${CADDYFILE}" <<'CADDY'
:80 {
    encode gzip

    handle_path /api* {
        reverse_proxy backend:8000
    }

    reverse_proxy frontend:80

    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer-when-downgrade"
    }
}
CADDY
fi

# --- Rechte & Ownership für sqlite/ ------------------------------------------
echo
echo "== SQLite-Verzeichnis prüfen"
# Hinweis: falls Backend als 'appuser' (UID 10001) läuft, Ownership setzen
if id -u appuser >/dev/null 2>&1; then
  sudo chown -R appuser:appuser "${SQLITE_DIR}" || true
else
  # default: dem aktuellen User geben; Container-User ist evtl. non-root,
  # Rechteproblem lässt sich mit named volume lösen (siehe Doku).
  chmod 755 "${SQLITE_DIR}" || true
fi

# --- Docker Compose (build & up) ---------------------------------------------
echo
echo "== Docker Compose Build & Start =="
docker compose build
docker compose up -d

echo
echo "== Fertig! =="
if [[ "${MODE}" == "1" ]]; then
  echo "Bitte stelle sicher, dass dein DNS (A-Record) auf diese Server-IP zeigt:"
  curl -s https://ipinfo.io/ip || true
  echo
  echo "Danach: https://${DOMAIN}/ aufrufen (Caddy holt automatisch das Zertifikat)."
else
  echo "Rufe jetzt im Browser auf:  http://<SERVER-IP>/"
fi

echo
echo "Logs (Proxy):  docker compose logs -f reverse-proxy"
echo "Logs (Backend): docker compose logs -f backend"
