# Design: Dashboard Performance + Café Tab Fix + Unified Bulk Pay

**Date:** 2026-06-26  
**Status:** Approved

---

## Summary

Three related fixes for the GameNet management system dashboard:

1. **Bug fix** — Session-linked café tab sales are excluded from `Session.remaining_amount` and `Session.is_fully_paid`, causing tabs to silently disappear after a session's play-time cost is paid.
2. **Feature** — Unified floating "Pay selected" bar that combines selections from both the unpaid sessions section and the pending free-tabs section.
3. **Performance** — Dashboard takes ~1 minute to load due to N+1 queries on sessions and device cards. Fix with DB annotations and `prefetch_related`.

---

## Issue 1 — Café Tab in Session Payment Amount

### Root Cause

`Session.remaining_amount` is:
```python
max(0, self.total_cost - self.paid_amount)
```
It does not include `Sale` records linked to the session with `payment_type='account'` and `customer=None` (session café tabs). After the play-time cost is paid, `is_fully_paid` returns `True` and the café tab debt vanishes from the dashboard with no way to collect it.

### Fix

Add `cafe_tab_total` property to `Session`:
```python
@property
def cafe_tab_total(self):
    return (self.sales.filter(payment_type='account', customer__isnull=True)
                      .aggregate(t=Sum('total_price'))['t'] or 0)
```

Update `remaining_amount`:
```python
@property
def remaining_amount(self):
    if self.total_cost is None:
        return 0
    return max(0, self.total_cost + self.cafe_tab_total - self.paid_amount)
```

`is_fully_paid` derives from `remaining_amount` — automatically fixed.

### View impact

- `session_pay` view: `total_with_tab`, `remaining_with_tab`, `fully_paid_with_tab` are still computed explicitly for UI display (showing the breakdown). The `pay_remaining_cash` / `pay_remaining_account` quick-actions use `session.remaining_amount`, which is now correct.
- `sessions_pay_bulk`: uses `s.remaining_amount` per session — now correct.
- Dashboard unpaid session cards: show `s.remaining_amount` — now correct.

### Performance note

Add `prefetch_related('sales')` wherever sessions are bulk-loaded so `cafe_tab_total` hits the prefetch cache, not the DB.

---

## Issue 2 — Unified Floating "Pay Selected" Bar

### Behaviour

Both existing sections (unpaid sessions, pending free tabs) keep their own checkboxes and their own single-section bulk-pay buttons. A new sticky bar fixed at the bottom of the viewport appears whenever any checkbox from either section is checked:

```
[💳 پرداخت ۳ مورد — ۲۲۲,۰۰۰ تومان]   [انصراف]
```

Clicking opens `#unifiedPayModal` — a single modal that accepts a mix of session IDs and sale IDs, shows payment type selector (cash / account), and submits to the new endpoint.

### New endpoint: `POST /pay/bulk/unified/`

View `unified_pay_bulk`:

- Receives `session_ids[]` and `sale_ids[]`
- Calculates combined total:  
  - Sessions: `s.remaining_amount` per session (now includes café tab)  
  - Free-tab sales: `sale.total_price` per sale
- For cash payment:  
  - Per session: `Payment(session=s, amount=s.remaining_amount, payment_type='cash')`  
  - Per sale: `sale.payment_type = 'cash'`, `Payment(amount=sale.total_price, payment_type='cash', notes=…)`
- For account payment:  
  - Checks `customer.can_add_debt(total)` first  
  - Per session: `Payment(session=s, customer=customer, amount=s.remaining_amount, payment_type='account_debit')`  
  - Per sale: `sale.payment_type = 'account'`, `sale.customer = customer`, `Payment(amount=..., payment_type='account_debit', customer=customer)`  
  - Single `customer.balance -= total` save at end
- Combined payment note: lists all device names and product names

### JS coordination

Global `updateUnifiedBar()` function called from both `updateSessionSelection()` and `updateTabSelection()`. It sums totals from both `.session-checkbox:checked` and `.tab-checkbox:checked` and updates bar visibility and label.

---

## Issue 3 — Dashboard Performance

### Root Cause

Two major N+1 patterns:

1. **`unpaid_sessions` in dashboard view**: loads ALL finished sessions via a Python list comprehension, then for each calls `s.is_fully_paid` → `s.remaining_amount` → `s.payments.all()` (1 extra query per session).
2. **Device cards**: `device.is_occupied` (1 query), `device.active_session` (1 query), `device.unpaid_session` (loops finished sessions checking `is_fully_paid` → N+1 per device) — all fire per-device with no caching.

With hundreds of sessions and ~20 devices, this produces 500–2000+ queries per page load.

### Fix A — Annotated unpaid_sessions query

Must include café tab in the DB filter, otherwise sessions where only the café tab is unpaid are missed.

Use correlated `Subquery` for both aggregations to avoid the SQL cross-join issue: annotating `Sum` on two different reverse FK relations in the same queryset joins both tables, multiplying counts and producing wrong sums for sessions that have both payments and café sales.

```python
from django.db.models import Q, Sum, F, OuterRef, Subquery
from django.db.models.functions import Coalesce

_paid_subq = (
    Payment.objects.filter(session=OuterRef('pk'))
    .values('session').annotate(t=Sum('amount')).values('t')
)
_cafe_subq = (
    Sale.objects.filter(session=OuterRef('pk'), payment_type='account', customer__isnull=True)
    .values('session').annotate(t=Sum('total_price')).values('t')
)

unpaid_sessions = list(
    Session.objects.filter(status='finished', total_cost__isnull=False)
    .annotate(
        paid_total=Coalesce(Subquery(_paid_subq), 0),
        cafe_tab=Coalesce(Subquery(_cafe_subq), 0),
    )
    .filter(paid_total__lt=F('total_cost') + F('cafe_tab'))
    .select_related('device')
    .prefetch_related('payments', 'sales')
    .order_by('-ended_at')
)
```

Three queries (sessions + 2 subqueries evaluated by DB) replace 1+N. `remaining_amount` uses the prefetch cache.

### Fix B — Device card prefetch

In both `dashboard` and `dashboard_cards` views:

```python
from django.db.models import Prefetch

devices_qs = (Device.objects.filter(is_active=True)
    .order_by('sort_order', 'device_type', 'name')
    .prefetch_related(
        Prefetch('sessions',
            queryset=Session.objects.filter(status='active')
                           .prefetch_related('players'),
            to_attr='active_sessions_cache'),
        Prefetch('sessions',
            queryset=Session.objects.filter(status='finished')
                           .prefetch_related('payments', 'sales')
                           .order_by('-ended_at'),
            to_attr='finished_sessions_cache'),
    ))
```

Update `Device` model properties to use cached lists when available:

```python
@property
def is_occupied(self):
    if hasattr(self, 'active_sessions_cache'):
        return len(self.active_sessions_cache) > 0
    return self.sessions.filter(status='active').exists()

@property
def active_session(self):
    if hasattr(self, 'active_sessions_cache'):
        return self.active_sessions_cache[0] if self.active_sessions_cache else None
    return self.sessions.filter(status='active').first()

@property
def unpaid_session(self):
    if hasattr(self, 'finished_sessions_cache'):
        for s in self.finished_sessions_cache:
            if not s.is_fully_paid:
                return s
        return None
    for s in self.sessions.filter(status='finished').order_by('-ended_at'):
        if not s.is_fully_paid:
            return s
    return None
```

All other views (`session_detail`, `shop`, etc.) continue to use the DB-query path unchanged.

### Fix C — Deferred card loading

Remove device card rendering from the initial dashboard HTML. Render a skeleton grid placeholder instead. Add `hx-trigger="load"` to fire `dashboard_cards` immediately after page paint:

```html
<div id="device-cards-section"
     hx-get="{% url 'dashboard_cards' %}"
     hx-trigger="load, every 120s"
     hx-swap="innerHTML">
  <!-- skeleton placeholder -->
</div>
```

Result: dashboard page appears in <1s with stats and unpaid sections visible; device cards populate in ~1–2s.

### Fix D — DB indexes (migration)

New `Meta.indexes` on:
- `Session`: `['status']`, `['device_id', 'status']`
- `Payment`: `['session_id']`
- `Sale`: `['session_id', 'payment_type']`

---

## Files Changed

| File | Change |
|---|---|
| `core/models.py` | Add `cafe_tab_total` to Session; update `remaining_amount`; update Device cache-aware properties |
| `core/views.py` | Fix `dashboard` and `dashboard_cards` to use annotated/prefetched queries; add `unified_pay_bulk` view |
| `config/urls.py` | Add route for `unified_pay_bulk` |
| `templates/dashboard.html` | Add unified floating pay bar + JS; deferred card skeleton; connect `updateUnifiedBar()` |
| `core/migrations/XXXX_add_indexes.py` | Add DB indexes |

---

## Out of Scope

- Migrating café tab Sale records to a different paid state after session payment (Sale objects remain with `payment_type='account'`; the Payment record covers the amount)
- Pagination of the unpaid sessions list
- Any other page performance (reports, customers, shop)
