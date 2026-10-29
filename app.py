"""
Expense Tracker
---------------
A small, self-contained Flask app for tracking monthly expenses against
an income and savings goal. Data is stored in a local SQLite file, so
there is nothing else to install or run.

Run directly for development:
    python app.py

Run in production (see README.md for a systemd unit):
    gunicorn -w 2 -b 127.0.0.1:8000 app:app
"""

import os
import re
import sqlite3
from datetime import datetime
from contextlib import contextmanager

from flask import Flask, g, jsonify, render_template, request

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("EXPENSE_TRACKER_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "expense_tracker.db")

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DEFAULT_CATEGORIES = [
    "Housing", "Utilities", "Groceries", "Transportation", "Insurance",
    "Health", "Debt Payments", "Dining Out", "Subscriptions",
    "Entertainment", "Personal", "Other",
]

app = Flask(__name__)


# ----------------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------------

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_settings (
            month TEXT PRIMARY KEY,
            income REAL NOT NULL DEFAULT 0,
            savings_goal REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)")
    conn.commit()
    conn.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ----------------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify({"error": err.message}), err.status


def require_month(value):
    if not value or not MONTH_RE.match(value):
        raise ApiError("Query param 'month' must be in YYYY-MM format.")
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise ApiError("Query param 'month' is not a valid calendar month.")
    return value


def current_month():
    return datetime.now().strftime("%Y-%m")


def parse_expense_payload(payload, partial=False):
    """Validate an incoming expense dict. When partial=True, missing keys
    are allowed (used for PATCH-style updates)."""
    out = {}

    if "date" in payload or not partial:
        date = str(payload.get("date", ""))
        if not DATE_RE.match(date):
            raise ApiError("Field 'date' must be in YYYY-MM-DD format.")
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise ApiError("Field 'date' is not a valid calendar date.")
        out["date"] = date

    if "category" in payload or not partial:
        category = str(payload.get("category", "")).strip()
        if not category:
            raise ApiError("Field 'category' is required.")
        out["category"] = category[:80]

    if "description" in payload or not partial:
        out["description"] = str(payload.get("description", "")).strip()[:280]

    if "amount" in payload or not partial:
        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            raise ApiError("Field 'amount' must be a number.")
        if amount <= 0:
            raise ApiError("Field 'amount' must be greater than zero.")
        out["amount"] = round(amount, 2)

    return out


# ----------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        current_month=current_month(),
        default_categories=DEFAULT_CATEGORIES,
    )


# ----------------------------------------------------------------------------
# API: settings (income / savings goal per month)
# ----------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def get_settings():
    month = require_month(request.args.get("month"))
    db = get_db()
    row = db.execute(
        "SELECT month, income, savings_goal FROM monthly_settings WHERE month = ?",
        (month,),
    ).fetchone()

    if row:
        return jsonify({"month": month, "income": row["income"],
                         "savings_goal": row["savings_goal"], "inherited": False})

    # No settings saved for this month yet -- offer the most recent prior
    # month's numbers as a starting point so the user isn't starting from
    # zero every month, but don't persist it until they confirm.
    prior = db.execute(
        """
        SELECT income, savings_goal FROM monthly_settings
        WHERE month < ? ORDER BY month DESC LIMIT 1
        """,
        (month,),
    ).fetchone()

    if prior:
        return jsonify({"month": month, "income": prior["income"],
                         "savings_goal": prior["savings_goal"], "inherited": True})

    return jsonify({"month": month, "income": 0, "savings_goal": 0, "inherited": True})


@app.route("/api/settings", methods=["PUT"])
def put_settings():
    payload = request.get_json(silent=True) or {}
    month = require_month(payload.get("month"))

    try:
        income = float(payload.get("income", 0))
        savings_goal = float(payload.get("savings_goal", 0))
    except (TypeError, ValueError):
        raise ApiError("'income' and 'savings_goal' must be numbers.")

    if income < 0 or savings_goal < 0:
        raise ApiError("'income' and 'savings_goal' cannot be negative.")

    db = get_db()
    db.execute(
        """
        INSERT INTO monthly_settings (month, income, savings_goal)
        VALUES (?, ?, ?)
        ON CONFLICT(month) DO UPDATE SET income = excluded.income,
                                          savings_goal = excluded.savings_goal
        """,
        (month, round(income, 2), round(savings_goal, 2)),
    )
    db.commit()
    return jsonify({"month": month, "income": round(income, 2),
                     "savings_goal": round(savings_goal, 2), "inherited": False})


# ----------------------------------------------------------------------------
# API: expenses
# ----------------------------------------------------------------------------

@app.route("/api/expenses", methods=["GET"])
def list_expenses():
    month = require_month(request.args.get("month"))
    db = get_db()
    rows = db.execute(
        """
        SELECT id, date, category, description, amount
        FROM expenses
        WHERE substr(date, 1, 7) = ?
        ORDER BY date DESC, id DESC
        """,
        (month,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/expenses", methods=["POST"])
def create_expense():
    payload = request.get_json(silent=True) or {}
    data = parse_expense_payload(payload)
    db = get_db()
    cur = db.execute(
        "INSERT INTO expenses (date, category, description, amount) VALUES (?, ?, ?, ?)",
        (data["date"], data["category"], data["description"], data["amount"]),
    )
    db.commit()
    row = db.execute(
        "SELECT id, date, category, description, amount FROM expenses WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/expenses/<int:expense_id>", methods=["PUT"])
def update_expense(expense_id):
    payload = request.get_json(silent=True) or {}
    data = parse_expense_payload(payload, partial=True)
    if not data:
        raise ApiError("No fields to update.")

    db = get_db()
    existing = db.execute("SELECT id FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not existing:
        raise ApiError("Expense not found.", status=404)

    fields = ", ".join(f"{k} = ?" for k in data)
    db.execute(f"UPDATE expenses SET {fields} WHERE id = ?", (*data.values(), expense_id))
    db.commit()
    row = db.execute(
        "SELECT id, date, category, description, amount FROM expenses WHERE id = ?",
        (expense_id,),
    ).fetchone()
    return jsonify(dict(row))


@app.route("/api/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    db = get_db()
    existing = db.execute("SELECT id FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not existing:
        raise ApiError("Expense not found.", status=404)
    db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    db.commit()
    return jsonify({"deleted": expense_id})


# ----------------------------------------------------------------------------
# API: summary (the budget math)
# ----------------------------------------------------------------------------

@app.route("/api/summary")
def summary():
    month = require_month(request.args.get("month"))
    db = get_db()

    settings_row = db.execute(
        "SELECT income, savings_goal FROM monthly_settings WHERE month = ?", (month,)
    ).fetchone()
    inherited = settings_row is None
    if settings_row is None:
        prior = db.execute(
            """
            SELECT income, savings_goal FROM monthly_settings
            WHERE month < ? ORDER BY month DESC LIMIT 1
            """,
            (month,),
        ).fetchone()
        income = prior["income"] if prior else 0
        savings_goal = prior["savings_goal"] if prior else 0
    else:
        income = settings_row["income"]
        savings_goal = settings_row["savings_goal"]

    total_row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE substr(date, 1, 7) = ?",
        (month,),
    ).fetchone()
    total_expenses = total_row["total"]

    by_category = db.execute(
        """
        SELECT category, COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE substr(date, 1, 7) = ?
        GROUP BY category
        ORDER BY total DESC
        """,
        (month,),
    ).fetchall()

    available_to_spend = income - savings_goal  # the actual spending budget
    remaining_budget = available_to_spend - total_expenses
    spent_pct = (total_expenses / available_to_spend * 100) if available_to_spend > 0 else None

    return jsonify({
        "month": month,
        "income": income,
        "savings_goal": savings_goal,
        "settings_inherited": inherited,
        "available_to_spend": round(available_to_spend, 2),
        "total_expenses": round(total_expenses, 2),
        "remaining_budget": round(remaining_budget, 2),
        "spent_pct": round(spent_pct, 1) if spent_pct is not None else None,
        "by_category": [{"category": r["category"], "amount": round(r["total"], 2)} for r in by_category],
    })


@app.route("/api/months")
def months_with_data():
    db = get_db()
    rows = db.execute(
        """
        SELECT month FROM (
            SELECT month FROM monthly_settings
            UNION
            SELECT substr(date, 1, 7) AS month FROM expenses
        )
        ORDER BY month DESC
        """
    ).fetchall()
    months = [r["month"] for r in rows]
    if current_month() not in months:
        months.insert(0, current_month())
    return jsonify(months)


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
