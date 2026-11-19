#!/usr/bin/env bash
#
# Install or upgrade Ledger as a native service on RHEL 8, 9 or 10
# (and compatible distributions such as Rocky Linux, AlmaLinux and Oracle Linux).
#
#   sudo ./deploy/install.sh
#
# Run it again after pulling new code to upgrade; the database and your
# edits to /etc/sysconfig/ledger and /etc/nginx/conf.d/ledger.conf are kept.
#
# Optional environment variables:
#   LEDGER_SERVER_NAME  names nginx answers to (default: hostname + IPv4 addresses)
#   LEDGER_BIND         gunicorn address on first install (default: 127.0.0.1:8000)
#   SKIP_NGINX=1        install only the app service, no nginx/firewall changes
#   LEDGER_AUTH_USER    login name for nginx password protection (default: prompt)
#   LEDGER_AUTH_PASSWORD  its password (default: prompt; needed when not on a terminal)
#   SKIP_AUTH=1         don't set up password protection
#
set -euo pipefail

APP_NAME=ledger
APP_USER=ledger
APP_DIR=/opt/ledger
DATA_DIR=/var/lib/ledger
SYSCONFIG=/etc/sysconfig/ledger
UNIT=/etc/systemd/system/ledger.service
NGINX_CONF=/etc/nginx/conf.d/ledger.conf
HTPASSWD=/etc/nginx/ledger.htpasswd

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root, e.g.: sudo $0"
[[ -f "$SRC_DIR/app.py" ]] || die "Cannot find app.py in $SRC_DIR"
command -v dnf >/dev/null || die "dnf not found; this script targets RHEL 8/9/10."

# --------------------------------------------------------------------------
# 1. Python (Flask 3.x needs 3.9 or newer)
# --------------------------------------------------------------------------
python_ok() {
    "$1" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
}

PYTHON=""
for candidate in python3.12 python3.11; do
    if command -v "$candidate" >/dev/null || dnf -y -q install "$candidate" >/dev/null 2>&1; then
        if python_ok "$candidate"; then PYTHON="$(command -v "$candidate")"; break; fi
    fi
done
if [[ -z $PYTHON ]]; then
    dnf -y -q install python3 >/dev/null
    python_ok python3 || die "Need Python 3.9+; install python3.11 or python3.12 from AppStream."
    PYTHON="$(command -v python3)"
fi
log "Using $("$PYTHON" --version) at $PYTHON"

# --------------------------------------------------------------------------
# 2. Service account
# --------------------------------------------------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
    log "Creating system user '$APP_USER'"
    useradd --system --home-dir "$DATA_DIR" --no-create-home \
            --shell /sbin/nologin --comment "Ledger expense tracker" "$APP_USER"
fi

# --------------------------------------------------------------------------
# 3. Application code: owned by root, read-only to the service
# --------------------------------------------------------------------------
log "Installing application to $APP_DIR"
install -d -m 0755 -o root -g root "$APP_DIR"
rm -rf "$APP_DIR/templates" "$APP_DIR/static"
cp -r "$SRC_DIR/templates" "$SRC_DIR/static" "$APP_DIR/"
install -m 0644 "$SRC_DIR/app.py" "$SRC_DIR/gunicorn.conf.py" \
                "$SRC_DIR/requirements.txt" "$SRC_DIR/README.md" "$APP_DIR/"
chown -R root:root "$APP_DIR/templates" "$APP_DIR/static"
chmod -R u=rwX,go=rX "$APP_DIR/templates" "$APP_DIR/static"

# Rebuild the venv if it's missing or was made with a different Python.
if [[ ! -x "$APP_DIR/venv/bin/python" ]] || \
   [[ "$("$APP_DIR/venv/bin/python" -c 'import sys; print(sys.version_info[:2])')" != \
      "$("$PYTHON" -c 'import sys; print(sys.version_info[:2])')" ]]; then
    log "Creating virtualenv"
    rm -rf "$APP_DIR/venv"
    "$PYTHON" -m venv "$APP_DIR/venv"
fi
log "Installing Python dependencies"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --------------------------------------------------------------------------
# 4. Configuration + systemd unit
# --------------------------------------------------------------------------
if [[ ! -f $SYSCONFIG ]]; then
    log "Writing $SYSCONFIG"
    install -m 0644 "$SRC_DIR/deploy/ledger.sysconfig" "$SYSCONFIG"
    if [[ -n ${LEDGER_BIND:-} ]]; then
        sed -i "s|^LEDGER_BIND=.*|LEDGER_BIND=${LEDGER_BIND}|" "$SYSCONFIG"
    fi
else
    log "Keeping existing $SYSCONFIG"
fi
BIND="$(sed -n 's/^LEDGER_BIND=//p' "$SYSCONFIG" | tail -n1)"
BIND="${BIND:-127.0.0.1:8000}"

log "Installing systemd unit"
install -m 0644 "$SRC_DIR/deploy/ledger.service" "$UNIT"

if command -v selinuxenabled >/dev/null && selinuxenabled; then
    restorecon -R "$APP_DIR" "$SYSCONFIG" "$UNIT" 2>/dev/null || true
fi

systemctl daemon-reload
systemctl enable --quiet "$APP_NAME"
systemctl restart "$APP_NAME"

log "Waiting for the app on $BIND"
for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null "http://$BIND/healthz"; then OK=1; break; fi
    sleep 1
done
[[ ${OK:-0} -eq 1 ]] || die "App did not respond. Check: journalctl -u $APP_NAME -n 50"

# --------------------------------------------------------------------------
# 5. nginx reverse proxy, SELinux and firewalld
# --------------------------------------------------------------------------
if [[ ${SKIP_NGINX:-0} != 1 ]]; then
    log "Setting up nginx"
    dnf -y -q install nginx >/dev/null

    if [[ ! -f $NGINX_CONF ]]; then
        IPV4S="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9.]+$' | tr '\n' ' ' || true)"
        SERVER_NAME="${LEDGER_SERVER_NAME:-$(hostname -f) ${IPV4S}}"
        SERVER_NAME="$(echo "$SERVER_NAME" | xargs)"
        sed -e "s|@SERVER_NAME@|${SERVER_NAME}|" -e "s|@UPSTREAM@|${BIND}|" \
            "$SRC_DIR/deploy/nginx-ledger.conf" > "$NGINX_CONF"
        chmod 0644 "$NGINX_CONF"
        log "nginx will answer to: $SERVER_NAME"
    else
        log "Keeping existing $NGINX_CONF"
    fi

    # ---- Password protection (HTTP basic auth) ----------------------------
    if [[ ${SKIP_AUTH:-0} == 1 ]]; then
        warn "SKIP_AUTH=1: password lines are commented out; anyone who can reach the site can use it."
        sed -i -E 's|^([[:space:]]*)(auth_basic(_user_file)?[[:space:]])|\1# \2|' "$NGINX_CONF"
    else
        dnf -y -q install httpd-tools >/dev/null
        if [[ ! -s $HTPASSWD ]]; then
            log "Creating the first login for the site ($HTPASSWD)"
            AUTH_USER="${LEDGER_AUTH_USER:-}"
            if [[ -z $AUTH_USER ]]; then
                [[ -t 0 ]] || die "Set LEDGER_AUTH_USER (and LEDGER_AUTH_PASSWORD) when not running on a terminal."
                read -rp "Username for the Ledger site: " AUTH_USER
            fi
            [[ $AUTH_USER =~ ^[A-Za-z0-9._@-]+$ ]] || die "Invalid username '$AUTH_USER'."
            if [[ -n ${LEDGER_AUTH_PASSWORD:-} ]]; then
                printf '%s' "$LEDGER_AUTH_PASSWORD" | htpasswd -c -i -B "$HTPASSWD" "$AUTH_USER"
            else
                [[ -t 0 ]] || die "Set LEDGER_AUTH_PASSWORD when not running on a terminal."
                htpasswd -c -B "$HTPASSWD" "$AUTH_USER"
            fi
        else
            log "Keeping existing logins in $HTPASSWD"
        fi
        chown root:nginx "$HTPASSWD"
        chmod 0640 "$HTPASSWD"

        # Turn the auth lines on in configs installed before auth was the default.
        sed -i -E 's|^([[:space:]]*)#[[:space:]]*(auth_basic(_user_file)?[[:space:]])|\1\2|' "$NGINX_CONF"
        grep -qE '^[[:space:]]*auth_basic_user_file' "$NGINX_CONF" || \
            warn "$NGINX_CONF has no auth_basic lines; add them by hand (see deploy/nginx-ledger.conf)."
    fi

    if command -v selinuxenabled >/dev/null && selinuxenabled; then
        log "SELinux: allowing nginx to connect to the app"
        setsebool -P httpd_can_network_connect 1
        restorecon "$NGINX_CONF"
        [[ -f $HTPASSWD ]] && restorecon "$HTPASSWD"
    fi

    nginx -t
    systemctl enable --quiet nginx
    systemctl reload-or-restart nginx

    if systemctl is-active --quiet firewalld; then
        log "firewalld: opening http/https"
        firewall-cmd --quiet --permanent --add-service=http --add-service=https
        firewall-cmd --quiet --reload
    fi
fi

log "Done."
echo
echo "  Service:  systemctl status $APP_NAME"
echo "  Logs:     journalctl -u $APP_NAME -f"
echo "  Database: $DATA_DIR/expense_tracker.db"
echo "  Config:   $SYSCONFIG"
if [[ ${SKIP_NGINX:-0} != 1 ]]; then
    echo "  URL:      http://$(hostname -f)/"
fi
