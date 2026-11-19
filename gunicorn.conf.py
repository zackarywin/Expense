"""
Gunicorn settings for running Ledger as a native service.

Every value can be overridden through environment variables, which the
systemd unit reads from /etc/sysconfig/ledger.
"""

import os

# Listen on loopback only; nginx is the public entry point.
bind = os.environ.get("LEDGER_BIND", "127.0.0.1:8000")

# SQLite serializes writes, so keep this small (2-4).
workers = int(os.environ.get("LEDGER_WORKERS", "2"))

timeout = int(os.environ.get("LEDGER_TIMEOUT", "30"))

# Log to stdout/stderr so everything ends up in journald.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LEDGER_LOG_LEVEL", "info")

# Worker heartbeat files go in RAM rather than on disk.
worker_tmp_dir = "/dev/shm"

# Trust X-Forwarded-* headers only from the local nginx.
forwarded_allow_ips = "127.0.0.1,::1"
