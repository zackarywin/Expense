(() => {
  "use strict";

  const state = {
    month: window.APP_BOOTSTRAP.currentMonth,
    inheritedPending: null, // {income, savings_goal} when current month has no saved settings
  };

  // ---- DOM refs -----------------------------------------------------------

  const el = {
    monthSelect: document.getElementById("month-select"),
    monthPrev: document.getElementById("month-prev"),
    monthNext: document.getElementById("month-next"),
    inheritedNote: document.getElementById("inherited-note"),
    confirmInherited: document.getElementById("confirm-inherited"),
    incomeInput: document.getElementById("income-input"),
    savingsInput: document.getElementById("savings-input"),
    spentValue: document.getElementById("spent-value"),
    remainingValue: document.getElementById("remaining-value"),
    remainingLine: document.getElementById("remaining-line"),
    tapeLabel: document.getElementById("tape-label"),
    tapePct: document.getElementById("tape-pct"),
    tapeFill: document.getElementById("tape-fill"),
    categoryBreakdown: document.getElementById("category-breakdown"),
    expenseForm: document.getElementById("expense-form"),
    formError: document.getElementById("form-error"),
    expenseRows: document.getElementById("expense-rows"),
    entriesEmpty: document.getElementById("entries-empty"),
    rowTemplate: document.getElementById("row-template"),
  };

  // ---- Helpers --------------------------------------------------------------

  function money(n) {
    const v = Number(n || 0);
    return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function monthLabel(monthStr) {
    const [y, m] = monthStr.split("-").map(Number);
    const d = new Date(y, m - 1, 1);
    return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  }

  function shiftMonth(monthStr, delta) {
    const [y, m] = monthStr.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      throw new Error((body && body.error) || `Request failed (${res.status})`);
    }
    return body;
  }

  // ---- Month select ----------------------------------------------------------

  async function populateMonthSelect() {
    let months = [];
    try {
      months = await api("/api/months");
    } catch (_) {
      months = [state.month];
    }
    if (!months.includes(state.month)) months.unshift(state.month);
    months.sort().reverse();
    renderMonthOptions(months);
  }

  function renderMonthOptions(months) {
    el.monthSelect.innerHTML = "";
    for (const m of months) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = monthLabel(m);
      if (m === state.month) opt.selected = true;
      el.monthSelect.appendChild(opt);
    }
  }

  function ensureMonthInSelect(monthStr) {
    if (![...el.monthSelect.options].some((o) => o.value === monthStr)) {
      const opt = document.createElement("option");
      opt.value = monthStr;
      opt.textContent = monthLabel(monthStr);
      el.monthSelect.appendChild(opt);
      const opts = [...el.monthSelect.options].sort((a, b) => (a.value < b.value ? 1 : -1));
      el.monthSelect.innerHTML = "";
      opts.forEach((o) => el.monthSelect.appendChild(o));
    }
    el.monthSelect.value = monthStr;
  }

  // ---- Summary / statement rendering -----------------------------------------

  async function loadSummary() {
    const summary = await api(`/api/summary?month=${state.month}`);

    el.incomeInput.value = summary.income;
    el.savingsInput.value = summary.savings_goal;
    el.spentValue.textContent = money(summary.total_expenses);
    el.remainingValue.textContent = money(Math.abs(summary.remaining_budget));
    if (summary.remaining_budget < 0) {
      el.remainingLine.classList.add("over");
      el.remainingValue.textContent = "-" + el.remainingValue.textContent;
    } else {
      el.remainingLine.classList.remove("over");
    }

    const available = summary.available_to_spend;
    el.tapeLabel.textContent = `$${money(summary.total_expenses)} of $${money(available)} available to spend`;
    const pct = summary.spent_pct === null ? 0 : summary.spent_pct;
    el.tapePct.textContent = `${Math.round(pct)}%`;
    el.tapeFill.style.width = `${Math.min(Math.max(pct, 0), 100)}%`;
    el.tapeFill.classList.toggle("over", summary.remaining_budget < 0);

    if (summary.settings_inherited && (summary.income > 0 || summary.savings_goal > 0)) {
      el.inheritedNote.hidden = false;
      state.inheritedPending = { income: summary.income, savings_goal: summary.savings_goal };
    } else {
      el.inheritedNote.hidden = true;
      state.inheritedPending = null;
    }

    renderCategoryBreakdown(summary.by_category, summary.total_expenses);
  }

  function renderCategoryBreakdown(categories, total) {
    el.categoryBreakdown.innerHTML = "";
    if (!categories || categories.length === 0) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = "No expenses logged this month yet.";
      el.categoryBreakdown.appendChild(p);
      return;
    }
    for (const c of categories) {
      const pct = total > 0 ? (c.amount / total) * 100 : 0;
      const row = document.createElement("div");
      row.className = "category-row";
      row.innerHTML = `
        <div class="category-row-top">
          <span class="cat-name">${escapeHtml(c.category)}</span>
          <span class="cat-amount">$${money(c.amount)}</span>
        </div>
        <div class="category-bar-track">
          <div class="category-bar-fill" style="width:${pct}%"></div>
        </div>
      `;
      el.categoryBreakdown.appendChild(row);
    }
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  async function saveSettings(income, savingsGoal) {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ month: state.month, income, savings_goal: savingsGoal }),
    });
    el.inheritedNote.hidden = true;
    state.inheritedPending = null;
  }

  // ---- Expense list ------------------------------------------------------------

  async function loadExpenses() {
    const rows = await api(`/api/expenses?month=${state.month}`);
    el.expenseRows.innerHTML = "";
    el.entriesEmpty.hidden = rows.length !== 0;
    for (const exp of rows) {
      el.expenseRows.appendChild(renderDisplayRow(exp));
    }
  }

  function renderDisplayRow(exp) {
    const tr = document.createElement("tr");
    tr.dataset.id = exp.id;
    tr.innerHTML = `
      <td class="col-date">${formatDate(exp.date)}</td>
      <td class="col-category"><span class="cell-category">${escapeHtml(exp.category)}</span></td>
      <td class="col-desc cell-desc">${escapeHtml(exp.description || "")}</td>
      <td class="col-amount cell-amount">$${money(exp.amount)}</td>
      <td class="col-actions">
        <div class="row-actions">
          <button type="button" class="icon-btn row-edit" title="Edit">&#9998;</button>
          <button type="button" class="icon-btn danger row-delete" title="Delete">&times;</button>
        </div>
      </td>
    `;
    tr.querySelector(".row-edit").addEventListener("click", () => switchToEditRow(tr, exp));
    tr.querySelector(".row-delete").addEventListener("click", () => deleteExpense(exp.id));
    return tr;
  }

  function formatDate(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function switchToEditRow(tr, exp) {
    const clone = el.rowTemplate.content.firstElementChild.cloneNode(true);
    clone.dataset.id = exp.id;
    clone.querySelector(".row-date").value = exp.date;
    clone.querySelector(".row-category").value = exp.category;
    clone.querySelector(".row-desc").value = exp.description || "";
    clone.querySelector(".row-amount").value = exp.amount;

    clone.querySelector(".row-save").addEventListener("click", async () => {
      try {
        const updated = await api(`/api/expenses/${exp.id}`, {
          method: "PUT",
          body: JSON.stringify({
            date: clone.querySelector(".row-date").value,
            category: clone.querySelector(".row-category").value,
            description: clone.querySelector(".row-desc").value,
            amount: clone.querySelector(".row-amount").value,
          }),
        });
        clone.replaceWith(renderDisplayRow(updated));
        await loadSummary();
      } catch (err) {
        alert(err.message);
      }
    });
    clone.querySelector(".row-delete").addEventListener("click", () => deleteExpense(exp.id));

    tr.replaceWith(clone);
  }

  async function deleteExpense(id) {
    if (!confirm("Delete this expense?")) return;
    try {
      await api(`/api/expenses/${id}`, { method: "DELETE" });
      await Promise.all([loadExpenses(), loadSummary()]);
    } catch (err) {
      alert(err.message);
    }
  }

  // ---- Load a month end-to-end -------------------------------------------------

  async function loadMonth(monthStr) {
    state.month = monthStr;
    ensureMonthInSelect(monthStr);
    el.formError.hidden = true;
    await Promise.all([loadSummary(), loadExpenses()]);
  }

  // ---- Event wiring --------------------------------------------------------------

  el.monthSelect.addEventListener("change", (e) => loadMonth(e.target.value));
  el.monthPrev.addEventListener("click", () => loadMonth(shiftMonth(state.month, -1)));
  el.monthNext.addEventListener("click", () => loadMonth(shiftMonth(state.month, 1)));

  el.confirmInherited.addEventListener("click", async () => {
    if (!state.inheritedPending) return;
    try {
      await saveSettings(state.inheritedPending.income, state.inheritedPending.savings_goal);
      await populateMonthSelect();
    } catch (err) {
      alert(err.message);
    }
  });

  el.incomeInput.addEventListener("change", async () => {
    try {
      await saveSettings(Number(el.incomeInput.value) || 0, Number(el.savingsInput.value) || 0);
      await loadSummary();
      await populateMonthSelect();
    } catch (err) {
      alert(err.message);
    }
  });

  el.savingsInput.addEventListener("change", async () => {
    try {
      await saveSettings(Number(el.incomeInput.value) || 0, Number(el.savingsInput.value) || 0);
      await loadSummary();
      await populateMonthSelect();
    } catch (err) {
      alert(err.message);
    }
  });

  el.expenseForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    el.formError.hidden = true;
    const formData = new FormData(el.expenseForm);
    const payload = {
      date: formData.get("date"),
      category: formData.get("category"),
      description: formData.get("description"),
      amount: formData.get("amount"),
    };
    try {
      await api("/api/expenses", { method: "POST", body: JSON.stringify(payload) });
      el.expenseForm.reset();
      document.getElementById("exp-date").value = defaultDateForMonth(state.month);
      await Promise.all([loadExpenses(), loadSummary()]);
      await populateMonthSelect();
    } catch (err) {
      el.formError.textContent = err.message;
      el.formError.hidden = false;
    }
  });

  function defaultDateForMonth(monthStr) {
    const today = new Date();
    const todayMonth = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
    if (monthStr === todayMonth) {
      return today.toISOString().slice(0, 10);
    }
    return `${monthStr}-01`;
  }

  // ---- Init ------------------------------------------------------------------------

  (async function init() {
    document.getElementById("exp-date").value = defaultDateForMonth(state.month);
    await populateMonthSelect();
    await loadMonth(state.month);
  })();
})();
