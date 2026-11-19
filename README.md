# Ledger — Expense Tracker

A small, self-hosted budget tool. Set your monthly income and savings goal,
log expenses as they happen, and see exactly what's left to spend. Data
lives in a local SQLite file — no external database or service required.

- **Backend:** Python 3 + Flask, JSON API
- **Storage:** SQLite (single file, auto-created on first run)
- **Frontend:** plain HTML/CSS/JS (no build step, no Node required)

## How the budget math works

For the selected month:

```
available to spend  = income − savings goal
remaining budget     = available to spend − total expenses logged
```

If you haven't set income/savings goal for a new month yet, the dashboard
shows last month's numbers as a starting point (labeled "inherited") until
you confirm them or edit them — it won't save anything until you do.

## 1. Local setup

Requires Python 3.9+.

```bash
cd expense-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python app.py
```

Visit `http://localhost:5000`. Data is stored in `data/expense_tracker.db`
(created automatically). Set `PORT=8080` to change the port, or
`FLASK_DEBUG=1` for auto-reload during development.

## 2. Deploying on Red Hat Enterprise Linux

Ledger runs natively on RHEL 8, 9 or 10 (or Rocky, Alma or Oracle Linux):
no Docker, Podman or Kubernetes is involved.

```
browser ──► nginx :80  ──►  gunicorn 127.0.0.1:8000  ──►  /var/lib/ledger/expense_tracker.db
            (systemd)       (systemd: ledger.service)
```

### Quick install

Copy the project to the server (`git clone`, `scp`, etc.), then:

```bash
cd Expense
sudo bash deploy/install.sh
```

The script:

1. Installs Python 3.12 (falls back to 3.11, then the system `python3` if it is 3.9 or newer) from AppStream.
2. Creates a `ledger` system user with no login shell.
3. Copies the app to `/opt/ledger` (owned by root, so the service can't change its own code) and builds a virtualenv in `/opt/ledger/venv`.
4. Installs `/etc/systemd/system/ledger.service` and `/etc/sysconfig/ledger`, then enables and starts the service.
5. Installs nginx with `/etc/nginx/conf.d/ledger.conf`, sets up password protection (see [Authentication](#authentication-password-protection)), turns on the SELinux boolean `httpd_can_network_connect` so nginx can reach gunicorn, and opens http/https in firewalld.

Optional settings:

```bash
sudo LEDGER_SERVER_NAME=ledger.example.com bash deploy/install.sh   # name nginx answers to
sudo LEDGER_BIND=127.0.0.1:8100 bash deploy/install.sh   # gunicorn port (first install)
sudo SKIP_NGINX=1 bash deploy/install.sh   # app service only
```

**Upgrading:** pull the new code and run `sudo bash deploy/install.sh` again.
It keeps the database and any changes you made to `/etc/sysconfig/ledger`
and `/etc/nginx/conf.d/ledger.conf`.

### Where things live

| What               | Path                                   |
|--------------------|----------------------------------------|
| Application code   | `/opt/ledger`                          |
| Virtualenv         | `/opt/ledger/venv`                     |
| Database           | `/var/lib/ledger/expense_tracker.db`   |
| Service settings   | `/etc/sysconfig/ledger`                |
| systemd unit       | `/etc/systemd/system/ledger.service`   |
| nginx site         | `/etc/nginx/conf.d/ledger.conf`        |
| Site logins        | `/etc/nginx/ledger.htpasswd`           |
| Logs               | `journalctl -u ledger`                 |

### Day-to-day commands

```bash
sudo systemctl status ledger
sudo systemctl restart ledger        # after editing /etc/sysconfig/ledger
sudo systemctl reload ledger         # graceful worker restart
journalctl -u ledger -f
curl http://127.0.0.1:8000/healthz
```

Keep `LEDGER_WORKERS` modest (2–4). SQLite handles concurrent reads well
but serializes writes, so run one instance per database file and don't scale
out across several servers.

### Manual install (without the script)

```bash
sudo dnf install -y python3.12 nginx
sudo useradd --system --home-dir /var/lib/ledger --no-create-home --shell /sbin/nologin ledger

sudo mkdir -p /opt/ledger
sudo cp -r app.py gunicorn.conf.py requirements.txt templates static /opt/ledger/
sudo python3.12 -m venv /opt/ledger/venv
sudo /opt/ledger/venv/bin/pip install -r /opt/ledger/requirements.txt

sudo cp deploy/ledger.service   /etc/systemd/system/ledger.service
sudo cp deploy/ledger.sysconfig /etc/sysconfig/ledger
sudo systemctl daemon-reload
sudo systemctl enable --now ledger

# nginx: edit @SERVER_NAME@ and @UPSTREAM@ (127.0.0.1:8000) after copying
sudo cp deploy/nginx-ledger.conf /etc/nginx/conf.d/ledger.conf
sudo dnf install -y httpd-tools
sudo htpasswd -c -B /etc/nginx/ledger.htpasswd yourname
sudo chown root:nginx /etc/nginx/ledger.htpasswd && sudo chmod 0640 /etc/nginx/ledger.htpasswd
sudo setsebool -P httpd_can_network_connect 1
sudo nginx -t && sudo systemctl enable --now nginx
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --reload
```

### HTTPS

If the server is reachable from the internet, add TLS with Certbot from EPEL:

```bash
sudo dnf install -y epel-release   # on RHEL: see https://docs.fedoraproject.org/en-US/epel/
sudo dnf install -y certbot python3-certbot-nginx
sudo certbot --nginx -d ledger.example.com
```

For an internal CA, add `listen 443 ssl;` together with `ssl_certificate` and
`ssl_certificate_key` to `/etc/nginx/conf.d/ledger.conf`.

### Authentication (password protection)

The app has no login of its own, so nginx guards it with a password
(HTTP basic auth). The installer installs `httpd-tools` and creates
`/etc/nginx/ledger.htpasswd` the first time it runs. It asks for a username
and password, or reads them from the environment:

```bash
sudo LEDGER_AUTH_USER=alex LEDGER_AUTH_PASSWORD='…' bash deploy/install.sh
```

Manage logins afterwards (changes apply immediately, no reload needed):

```bash
sudo htpasswd -B /etc/nginx/ledger.htpasswd alice     # add a user or change a password
sudo htpasswd -D /etc/nginx/ledger.htpasswd alice     # remove a user
```

Basic auth sends the password with every request, so **turn on HTTPS**
(above) before using the app over anything but a trusted network. To turn
protection off, run the installer with `SKIP_AUTH=1` (not recommended).

### Troubleshooting

- **502 Bad Gateway:** usually SELinux blocking nginx. Check
  `getsebool httpd_can_network_connect` (it should be `on`), and look for
  denials with `sudo ausearch -m avc -ts recent`.
- **The default nginx/RHEL test page appears:** the name in your browser's
  address bar isn't in `server_name` in `/etc/nginx/conf.d/ledger.conf`. Add
  it and reload nginx.
- **Login prompt keeps coming back:** wrong username or password. Reset it
  with `sudo htpasswd -B /etc/nginx/ledger.htpasswd <user>`. A 500 error
  instead usually means nginx can't read the file: check that it is
  `root:nginx` with mode `0640`.
- **The service won't start:** run `journalctl -u ledger -n 50`.
- **Fonts look plain:** the page loads IBM Plex from Google Fonts. On a
  server without internet access, the browser falls back to system fonts.
  Everything else still works.

### Moving data from the old Kubernetes deployment

Copy `expense_tracker.db` out of the old PVC (for example
`kubectl -n ledger cp <pod>:/data/expense_tracker.db ./expense_tracker.db`),
then on the RHEL server:

```bash
sudo systemctl stop ledger
sudo cp expense_tracker.db /var/lib/ledger/
sudo chown ledger:ledger /var/lib/ledger/expense_tracker.db
sudo restorecon -v /var/lib/ledger/expense_tracker.db
sudo systemctl start ledger
```

## 3. Backups

Everything lives in one file. To get a consistent copy while the app is
running, use SQLite's online backup:

```bash
sudo dnf install -y sqlite
sudo sqlite3 /var/lib/ledger/expense_tracker.db ".backup '/root/ledger-$(date +%F).db'"
```

To keep regular copies, schedule this with a cron job or a systemd timer and
delete old copies as you go.

## API reference

All endpoints are under `/api` and return JSON.

| Method | Path                    | Description                              |
|--------|--------------------------|-------------------------------------------|
| GET    | `/api/summary?month=YYYY-MM` | Income, savings goal, spent, remaining, category breakdown |
| GET    | `/api/settings?month=YYYY-MM` | Income/savings goal for a month |
| PUT    | `/api/settings`          | Body: `{month, income, savings_goal}` |
| GET    | `/api/expenses?month=YYYY-MM` | List expenses for a month |
| POST   | `/api/expenses`          | Body: `{date, category, description, amount}` |
| PUT    | `/api/expenses/<id>`     | Partial update, same fields as POST |
| DELETE | `/api/expenses/<id>`     | Delete an expense |
| GET    | `/api/months`            | Months that have any settings or expenses |
| GET    | `/healthz`               | Health check (app + database reachable) |

## Project layout

```
expense-tracker/
├── app.py              # Flask app: routes, validation, budget math
├── gunicorn.conf.py    # Production server settings (env-overridable)
├── requirements.txt
├── deploy/
│   ├── install.sh          # RHEL installer / upgrader
│   ├── ledger.service      # systemd unit
│   ├── ledger.sysconfig    # -> /etc/sysconfig/ledger
│   └── nginx-ledger.conf   # -> /etc/nginx/conf.d/ledger.conf
├── templates/
│   └── index.html      # Dashboard markup
├── static/
│   ├── style.css
│   └── app.js           # All dashboard interactivity (fetch-based)
└── data/                # Dev-only SQLite DB (production: /var/lib/ledger)
```
