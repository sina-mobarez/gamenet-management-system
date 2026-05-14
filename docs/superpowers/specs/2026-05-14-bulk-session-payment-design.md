# Bulk Session Payment

**Date:** 2026-05-14
**Status:** Approved

## Problem

The dashboard shows unpaid (finished) sessions individually, each with a single "💳 پرداخت" button that navigates to the full `session_pay` page. When multiple sessions need settling at once, staff must visit each pay page one by one. The shop's free-tab section already solves this with checkboxes + a bulk-pay modal — sessions need the same.

## Goal

Allow staff to select multiple unpaid sessions on the dashboard and pay all of them in one action (cash or account debit), without leaving the dashboard.

## Scope

- Dashboard UI changes only (no new pages)
- Supports cash and account debit payment types
- Redirects to dashboard after success
- Individual "💳 پرداخت" per-session link remains for cases where partial payment or detail review is needed

## Design

### UI — Dashboard unpaid sessions card

The unpaid sessions card (`{% if unpaid_sessions %}` block, `dashboard.html` lines 105–130) gains:

- **Checkbox** on each session card: `data-session-id="{{ s.pk }}"`, `data-amount="{{ s.remaining_amount }}"`
- **Header controls** (mirroring the shop tabs header):
  - "همه" select-all checkbox (`id="selectAllSessions"`)
  - Bulk pay button (`id="bulkSessionPayBtn"`) — disabled until ≥1 checked; label shows `پرداخت X مورد — Y تومان`
- Individual "💳 پرداخت" link is kept on each card

### Modal — `bulkSessionPayModal`

Mirrors `bulkTabPayModal`. Contains:

- Summary line: selected session count + total remaining (injected by JS on modal open)
- Payment type `<select>` (`name="pay_type"`): نقد / حساب
- Customer dropdown (TomSelect, hidden until "حساب" selected, `name="customer_id"`)
- Notes field (`name="notes"`)
- Hidden `session_ids[]` inputs injected by JS before submit
- Form posts to `/sessions/bulk-pay/`

### JS

Three functions added to `dashboard.html`:

| Function | Purpose |
|---|---|
| `updateSessionSelection()` | Recomputes total remaining from checked boxes, updates button label, enables/disables button |
| `toggleSelectAllSessions(cb)` | Checks/unchecks all `.session-checkbox` elements, calls `updateSessionSelection()` |
| `prepareBulkSessionModal()` | Clears old hidden inputs, injects one `<input type="hidden" name="session_ids[]">` per checked box, updates modal summary text |

Customer dropdown toggle reuses the same pattern as `toggleTabCustomer()`.

### Backend — `sessions_pay_bulk` view

**URL:** `POST /sessions/bulk-pay/`

**Logic:**

1. Read `session_ids[]`, `pay_type`, `customer_id`, `notes` from POST
2. If no IDs → error message, redirect dashboard
3. Fetch `Session.objects.filter(pk__in=session_ids, status='finished')`, skip already fully-paid sessions
4. If nothing remains → error, redirect dashboard
5. Compute `total = sum(s.remaining_amount for s in sessions)`
6. Build `base_note = "تسویه گروهی: <device names>" + optional user note`

**Cash path:**
- For each session: `Payment.objects.create(session=s, amount=s.remaining_amount, payment_type='cash', notes=base_note)`
- Success message with total + count → redirect dashboard

**Account path:**
- Resolve customer; call `customer.can_add_debt(total)` — if blocked, error + redirect
- For each session: `customer.balance -= s.remaining_amount` (accumulated in loop), `Payment.objects.create(session=s, customer=customer, amount=s.remaining_amount, payment_type='account_debit', notes=base_note)`
- One `customer.save()` after the loop
- Success message → redirect dashboard

### Data consistency

Each session gets its own `Payment` record so `session.paid_amount` (which queries `self.payments.aggregate(Sum('amount'))`) correctly reflects the payment. Sessions with `remaining_amount == 0` after payment will no longer appear in `unpaid_sessions` on the next dashboard load.

The shared `base_note` string ties the records together for audit purposes without requiring a schema change.

## Files changed

| File | Change |
|---|---|
| `config/urls.py` | Add `path('sessions/bulk-pay/', views.sessions_pay_bulk, name='sessions_pay_bulk')` |
| `core/views.py` | Add `sessions_pay_bulk` view function |
| `templates/dashboard.html` | Add checkboxes to session cards, header controls, modal, JS functions |

## Out of scope

- No model migrations required
- No new pages
- No changes to the existing `session_pay` detail page
- No partial bulk payment (bulk always pays the full remaining balance of each selected session)
