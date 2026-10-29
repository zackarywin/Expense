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

## 2. Running in production on Linux

Don't use the Flask dev server in production — use gunicorn behind a
reverse proxy.

### a) Install into a fixed location

```bash
sudo mkdir -p /opt/ledger
sudo cp -r . /opt/ledger
cd /opt/ledger
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Create a system user to run the service (no login shell needed)
sudo useradd --system --home /opt/ledger --shell /usr/sbin/nologin ledger
sudo chown -R ledger:ledger /opt/ledger
```

### b) Run with gunicorn

```bash
/opt/ledger/.venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
```

`-w 2` runs 2 worker processes. Because writes go through SQLite, keep the
worker count modest (2–4) rather than scaling out horizontally — SQLite
handles concurrent reads well but serializes writes.

### c) Run it as a systemd service

Create `/etc/systemd/system/ledger.service`:

```ini
[Unit]
Description=Ledger expense tracker
After=network.target

[Service]
Type=simple
User=ledger
Group=ledger
WorkingDirectory=/opt/ledger
Environment="EXPENSE_TRACKER_DATA_DIR=/opt/ledger/data"
ExecStart=/opt/ledger/.venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ledger
sudo systemctl status ledger
```

Logs: `journalctl -u ledger -f`

### d) Put nginx in front of it (recommended)

This gets you TLS and lets you use a normal domain/port. Example server
block, e.g. `/etc/nginx/sites-available/ledger`:

```nginx
server {
    listen 80;
    server_name ledger.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ledger /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Then add HTTPS with `certbot --nginx -d ledger.example.com` (via
[Certbot](https://certbot.eff.org/)) if this is reachable from the internet.

This app has no authentication built in — if it's exposed beyond your own
machine or LAN, put it behind a login (e.g. nginx `auth_basic`, a VPN, or a
reverse proxy with auth) since anyone who can reach it can see and edit
your data.

## 3. Backups

Everything lives in one file, so backing up is just copying it:

```bash
cp /opt/ledger/data/expense_tracker.db ~/backups/ledger-$(date +%F).db
```

Consider a nightly cron job that does this and rotates old copies.

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

## Project layout

```
expense-tracker/
├── app.py              # Flask app: routes, validation, budget math
├── requirements.txt
├── templates/
│   └── index.html      # Dashboard markup
├── static/
│   ├── style.css
│   └── app.js           # All dashboard interactivity (fetch-based)
└── data/                # SQLite DB lives here (created at runtime)
```
