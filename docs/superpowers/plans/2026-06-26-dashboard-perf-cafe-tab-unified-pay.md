# Dashboard Performance + Café Tab Fix + Unified Bulk Pay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a silent payment bug (café tab excluded from session total), add a unified floating bulk-pay bar across both dashboard sections, and eliminate N+1 queries that make the dashboard take ~1 minute to load.

**Architecture:** All changes stay inside the single `core` Django app. Model properties gain prefetch-cache awareness. Views switch from Python-level filtering to annotated DB queries. The dashboard defers device card rendering to HTMX so the page paints in <1s. A new `unified_pay_bulk` view handles mixed session+tab payments.

**Tech Stack:** Django 4.x, SQLite, HTMX (already in base template), Bootstrap RTL, TomSelect (already loaded in dashboard)

## Global Constraints

- All user-facing text must be in Persian (Farsi)
- Bootstrap RTL layout — right-to-left
- No new dependencies — use what's already in the project
- Run tests with: `python manage.py test core`
- Dev server: `python manage.py runserver`
- Default login: `admin` / `admin123`

---

## File Map

| File | What changes |
|---|---|
| `core/models.py` | Add `cafe_tab_total` to Session; update `remaining_amount`; add `Meta.indexes` to Session/Payment/Sale; cache-aware `is_occupied`, `active_session`, `unpaid_session` on Device |
| `core/tests.py` | Add tests for café tab model fix and unified_pay_bulk view |
| `core/views.py` | Fix `dashboard` and `dashboard_cards` queries; fix `sessions_pay_bulk` prefetch; add `unified_pay_bulk` |
| `config/urls.py` | Add route for `unified_pay_bulk` |
| `templates/dashboard.html` | Deferred card skeleton; unified floating bar + modal + JS |

---

## Task 1: Fix Session model — café tab in remaining_amount

**Files:**
- Modify: `core/models.py`
- Modify: `core/tests.py`

**What this fixes:** `Session.remaining_amount` and `Session.is_fully_paid` currently ignore café tab sales (Sales linked to the session with `payment_type='account'` and `customer=None`). After paying the play-time cost, `is_fully_paid` returns True and the café debt is lost silently.

- [ ] **Step 1: Add import for Sum to models.py**

Open `core/models.py`. The file currently starts with:
```python
from django.db import models
from django.utils import timezone
```

Add `Sum` import:
```python
from django.db import models
from django.db.models import Sum
from django.utils import timezone
```

- [ ] **Step 2: Write failing tests first**

Open `core/tests.py`. The file already imports `Device, Session, Customer, Payment`. Add `Sale, Product, ProductCategory` to that import and add a new test class at the bottom:

```python
from .models import Device, Session, Customer, Payment, Sale, Product, ProductCategory
```

Add at the bottom of `core/tests.py`:

```python
class SessionCafeTabTest(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            name='PS5-1', device_type='ps5', price_per_hour=60000
        )
        cat = ProductCategory.objects.create(name='نوشیدنی')
        self.product = Product.objects.create(
            name='نوشابه', price=8000, stock=100, category=cat
        )

    def _make_finished_session(self, total_cost):
        return Session.objects.create(
            device=self.device, session_type='free',
            started_at=timezone.now(), ended_at=timezone.now(),
            status='finished', total_cost=total_cost,
        )

    def _add_tab(self, session, qty=1):
        return Sale.objects.create(
            product=self.product, quantity=qty, unit_price=8000,
            total_price=8000 * qty, payment_type='account',
            session=session, customer=None,
        )

    def test_cafe_tab_total_zero_with_no_sales(self):
        s = self._make_finished_session(100000)
        self.assertEqual(s.cafe_tab_total, 0)

    def test_cafe_tab_total_sums_linked_account_sales(self):
        s = self._make_finished_session(100000)
        self._add_tab(s, qty=2)          # 16 000
        self.assertEqual(s.cafe_tab_total, 16000)

    def test_cafe_tab_total_excludes_cash_sales(self):
        s = self._make_finished_session(100000)
        Sale.objects.create(
            product=self.product, quantity=1, unit_price=8000,
            total_price=8000, payment_type='cash', session=s, customer=None,
        )
        self.assertEqual(s.cafe_tab_total, 0)

    def test_remaining_amount_includes_cafe_tab(self):
        s = self._make_finished_session(100000)
        self._add_tab(s, qty=2)          # 16 000 café tab
        # No payments: remaining = total_cost + cafe_tab
        self.assertEqual(s.remaining_amount, 116000)

    def test_is_fully_paid_requires_cafe_tab_covered(self):
        s = self._make_finished_session(100000)
        self._add_tab(s)                 # 8 000 café tab
        # Pay only the session cost — should still be unpaid
        Payment.objects.create(session=s, amount=100000, payment_type='cash')
        self.assertFalse(s.is_fully_paid)

    def test_is_fully_paid_true_when_all_covered(self):
        s = self._make_finished_session(100000)
        self._add_tab(s)                 # 8 000 café tab
        Payment.objects.create(session=s, amount=108000, payment_type='cash')
        s_fresh = Session.objects.get(pk=s.pk)
        self.assertTrue(s_fresh.is_fully_paid)
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
python manage.py test core.tests.SessionCafeTabTest -v 2
```

Expected: several FAIL with `AssertionError` because `cafe_tab_total` attribute doesn't exist yet.

- [ ] **Step 4: Add cafe_tab_total property to Session**

In `core/models.py`, the `Session` class has `paid_amount` at around line 137. Add `cafe_tab_total` right after `paid_amount`:

```python
    @property
    def paid_amount(self):
        return sum(p.amount for p in self.payments.all())

    @property
    def cafe_tab_total(self):
        return (
            self.sales.filter(payment_type='account', customer__isnull=True)
                      .aggregate(t=Sum('total_price'))['t'] or 0
        )
```

- [ ] **Step 5: Update remaining_amount to include café tab**

The current `remaining_amount` property (around line 141):

```python
    @property
    def remaining_amount(self):
        if self.total_cost is None:
            return 0
        return max(0, self.total_cost - self.paid_amount)
```

Replace with:

```python
    @property
    def remaining_amount(self):
        if self.total_cost is None:
            return 0
        return max(0, self.total_cost + self.cafe_tab_total - self.paid_amount)
```

`is_fully_paid` derives from `remaining_amount` — no change needed there.

- [ ] **Step 6: Run tests — expect PASS**

```bash
python manage.py test core.tests.SessionCafeTabTest -v 2
```

Expected: all 6 tests PASS.

- [ ] **Step 7: Run the full test suite to check nothing broke**

```bash
python manage.py test core -v 2
```

Expected: all tests PASS (the existing `BulkSessionPayTest` tests should still pass since those sessions have no café tab sales and `cafe_tab_total` returns 0).

- [ ] **Step 8: Commit**

```bash
git add core/models.py core/tests.py
git commit -m "fix: include café tab sales in Session.remaining_amount and is_fully_paid"
```

---

## Task 2: Add DB indexes via migration

**Files:**
- Modify: `core/models.py` (add `Meta` classes)
- Create: migration (auto-generated)

**What this does:** Adds indexes on the most-queried fields so the annotated queries in later tasks use index scans rather than full table scans.

- [ ] **Step 1: Add Meta.indexes to Session**

In `core/models.py`, the `Session` class currently has no `Meta`. Add it before the closing of the class (after the `is_fully_paid` property, before `class SessionPlayer`):

```python
    class Meta:
        indexes = [
            models.Index(fields=['status'],           name='session_status_idx'),
            models.Index(fields=['device', 'status'], name='session_device_status_idx'),
        ]
```

- [ ] **Step 2: Add Meta.indexes to Payment**

The `Payment` class currently has no `Meta`. Add after the `__str__` method:

```python
    class Meta:
        indexes = [
            models.Index(fields=['session'], name='payment_session_idx'),
        ]
```

- [ ] **Step 3: Add Meta.indexes to Sale**

The `Sale` class has a `save()` method but no `Meta`. Add after `save()`:

```python
    class Meta:
        indexes = [
            models.Index(fields=['session', 'payment_type'], name='sale_session_paytype_idx'),
        ]
```

- [ ] **Step 4: Generate and apply migration**

```bash
python manage.py makemigrations core --name add_query_indexes
python manage.py migrate
```

Expected output of `makemigrations`: `Migrations for 'core': core/migrations/000X_add_query_indexes.py — Add index ... (3 indexes total)`

- [ ] **Step 5: Run tests**

```bash
python manage.py test core -v 2
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/models.py core/migrations/
git commit -m "perf: add DB indexes on Session.status, Payment.session, Sale.session+payment_type"
```

---

## Task 3: Make Device properties use prefetch cache

**Files:**
- Modify: `core/models.py`

**What this does:** When the view attaches `active_sessions_cache` and `finished_sessions_cache` via `prefetch_related(..., to_attr=...)`, the current `Device.is_occupied`, `Device.active_session`, and `Device.unpaid_session` properties ignore the cache and hit the DB anyway. This task makes them check the cache first. The existing DB-query fallback is preserved so all other views (session_detail, shop, etc.) continue to work unchanged.

- [ ] **Step 1: Replace Device.is_occupied**

Current `is_occupied` in `core/models.py`:

```python
    @property
    def is_occupied(self):
        return self.sessions.filter(status='active').exists()
```

Replace with:

```python
    @property
    def is_occupied(self):
        if hasattr(self, 'active_sessions_cache'):
            return bool(self.active_sessions_cache)
        return self.sessions.filter(status='active').exists()
```

- [ ] **Step 2: Replace Device.active_session**

Current `active_session`:

```python
    @property
    def active_session(self):
        return self.sessions.filter(status='active').first()
```

Replace with:

```python
    @property
    def active_session(self):
        if hasattr(self, 'active_sessions_cache'):
            return self.active_sessions_cache[0] if self.active_sessions_cache else None
        return self.sessions.filter(status='active').first()
```

- [ ] **Step 3: Replace Device.unpaid_session**

Current `unpaid_session`:

```python
    @property
    def unpaid_session(self):
        for s in self.sessions.filter(status='finished').order_by('-ended_at'):
            if not s.is_fully_paid:
                return s
        return None
```

Replace with:

```python
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

- [ ] **Step 4: Run tests**

```bash
python manage.py test core -v 2
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/models.py
git commit -m "perf: make Device properties use prefetch cache when available"
```

---

## Task 4: Fix view queries — annotated unpaid_sessions + prefetch device cards

**Files:**
- Modify: `core/views.py`

**What this does:**
1. `dashboard`: replaces the Python-loop `unpaid_sessions` (N+1 queries) with one annotated DB query that correctly filters by total cost + café tab vs paid amount.
2. `dashboard` and `dashboard_cards`: attach `active_sessions_cache` and `finished_sessions_cache` via `Prefetch` so Task 3's cache-aware properties work.
3. `sessions_pay_bulk`: adds `'sales'` to `prefetch_related` so `remaining_amount` (which now reads `cafe_tab_total`) hits the cache.

- [ ] **Step 1: Update imports at top of views.py**

Current import line (around line 8):
```python
from django.db.models import Sum, Count, Q
```

Replace with:
```python
from django.db.models import Sum, Count, Q, F, Prefetch, OuterRef, Subquery
from django.db.models.functions import Coalesce
```

- [ ] **Step 2: Replace unpaid_sessions query in dashboard view**

Find the `dashboard` function (around line 40). The current `unpaid_sessions` assignment:

```python
    unpaid_sessions = [
        s for s in Session.objects.filter(status='finished')
                          .select_related('device').order_by('-ended_at')
        if not s.is_fully_paid
    ]
```

Replace with (note: use `Subquery` for both aggregations to avoid a SQL cross-join that multiplies sums when a session has both payments and café sales):

```python
    from django.db.models import OuterRef, Subquery

    _paid_subq = (
        Payment.objects.filter(session=OuterRef('pk'))
        .values('session')
        .annotate(t=Sum('amount'))
        .values('t')
    )
    _cafe_subq = (
        Sale.objects.filter(
            session=OuterRef('pk'),
            payment_type='account',
            customer__isnull=True,
        )
        .values('session')
        .annotate(t=Sum('total_price'))
        .values('t')
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

- [ ] **Step 3: Replace devices_qs in dashboard view**

In the same `dashboard` function, the current `devices_qs` line:

```python
    devices_qs = Device.objects.filter(is_active=True).order_by('sort_order', 'device_type', 'name')
```

Replace with:

```python
    devices_qs = (
        Device.objects.filter(is_active=True)
        .order_by('sort_order', 'device_type', 'name')
        .prefetch_related(
            Prefetch(
                'sessions',
                queryset=Session.objects.filter(status='active').prefetch_related('players'),
                to_attr='active_sessions_cache',
            ),
            Prefetch(
                'sessions',
                queryset=Session.objects.filter(status='finished')
                               .prefetch_related('payments', 'sales')
                               .order_by('-ended_at'),
                to_attr='finished_sessions_cache',
            ),
        )
    )
```

- [ ] **Step 4: Replace dashboard_cards view**

Find `dashboard_cards` (around line 100). Replace the entire function body:

```python
@login_required
def dashboard_cards(request):
    MERGED_TYPES = {'billiard': 'بیلیارد / اسنوکر', 'snooker': 'بیلیارد / اسنوکر'}
    device_groups = {}
    devices = (
        Device.objects.filter(is_active=True)
        .order_by('sort_order', 'device_type', 'name')
        .prefetch_related(
            Prefetch(
                'sessions',
                queryset=Session.objects.filter(status='active').prefetch_related('players'),
                to_attr='active_sessions_cache',
            ),
            Prefetch(
                'sessions',
                queryset=Session.objects.filter(status='finished')
                               .prefetch_related('payments', 'sales')
                               .order_by('-ended_at'),
                to_attr='finished_sessions_cache',
            ),
        )
    )
    for d in devices:
        key = MERGED_TYPES.get(d.device_type, d.get_device_type_display())
        device_groups.setdefault(key, []).append(d)
    return render(request, 'partials/device_cards.html', {'device_groups': device_groups.items()})
```

- [ ] **Step 5: Fix sessions_pay_bulk prefetch**

Find `sessions_pay_bulk` (around line 589). The current sessions loading:

```python
    sessions = [
        s for s in Session.objects.filter(pk__in=session_ids, status='finished')
                          .select_related('device')
                          .prefetch_related('payments')
        if not s.is_fully_paid
    ]
```

Replace with:

```python
    sessions = [
        s for s in Session.objects.filter(pk__in=session_ids, status='finished')
                          .select_related('device')
                          .prefetch_related('payments', 'sales')
        if not s.is_fully_paid
    ]
```

- [ ] **Step 6: Run tests**

```bash
python manage.py test core -v 2
```

Expected: all tests PASS.

- [ ] **Step 7: Manual smoke-test**

Start the server and open the dashboard. With some unpaid sessions in the DB, the page should load noticeably faster. The browser Network tab should show the dashboard response time dropping from seconds/minutes to well under 1s.

```bash
python manage.py runserver
```

- [ ] **Step 8: Commit**

```bash
git add core/views.py
git commit -m "perf: replace N+1 unpaid_sessions query with annotation; prefetch device cards"
```

---

## Task 5: Deferred device card loading

**Files:**
- Modify: `templates/dashboard.html`

**What this does:** Removes device card server-side rendering from the initial page load. The page paints immediately with a skeleton grid; HTMX fires `dashboard_cards` right after paint (and still every 120s). Users see stats and unpaid items in <1s instead of waiting for all device queries to complete.

- [ ] **Step 1: Replace the device cards section in dashboard.html**

Find this block in `templates/dashboard.html` (around line 206):

```html
<!-- ── Device cards — HTMX polls every 120s to sync with other tablets ── -->
<div id="device-cards-section"
     hx-get="{% url 'dashboard_cards' %}"
     hx-trigger="every 120s"
     hx-swap="innerHTML">
  {% include 'partials/device_cards.html' %}
</div>
```

Replace with:

```html
<!-- ── Device cards — loads immediately after paint, then every 120s ── -->
<div id="device-cards-section"
     hx-get="{% url 'dashboard_cards' %}"
     hx-trigger="load, every 120s"
     hx-swap="innerHTML">
  <!-- skeleton shown until HTMX loads real cards -->
  <div class="mb-4">
    <div class="device-grid">
      {% for i in "123456" %}
      <div class="device-col">
        <div class="device-card free h-100" style="opacity:.35;pointer-events:none">
          <div style="height:14px;background:rgba(255,255,255,.1);border-radius:4px;margin-bottom:14px;width:50%"></div>
          <div style="height:44px;background:rgba(255,255,255,.06);border-radius:6px;margin-bottom:10px"></div>
          <div style="height:30px;background:rgba(255,255,255,.05);border-radius:6px"></div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
</div>
```

- [ ] **Step 2: Verify in browser**

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. The page should appear instantly with 6 skeleton cards, then real cards should replace them within 1–2 seconds.

- [ ] **Step 3: Commit**

```bash
git add templates/dashboard.html
git commit -m "perf: defer device card rendering via HTMX load trigger; show skeleton"
```

---

## Task 6: Add unified_pay_bulk view and URL

**Files:**
- Modify: `core/views.py`
- Modify: `config/urls.py`
- Modify: `core/tests.py`

**What this does:** New endpoint `POST /pay/bulk/unified/` that accepts mixed `session_ids[]` and `sale_ids[]` (free tabs only, not session-linked tabs), calculates the combined total, and settles everything in one action with cash or customer-account payment.

- [ ] **Step 1: Write failing tests first**

Add the following class to `core/tests.py`. Add these imports if not already at the top of the file (they were added in Task 1):

```python
# Ensure these are imported (from Task 1):
from .models import Device, Session, Customer, Payment, Sale, Product, ProductCategory
```

Add at the bottom of `core/tests.py`:

```python
class UnifiedPayBulkTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('staff2', password='pass')
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            name='PS4-1', device_type='ps4', price_per_hour=50000
        )
        cat = ProductCategory.objects.create(name='خوراکی')
        self.product = Product.objects.create(
            name='چیپس', price=10000, stock=50, category=cat
        )

    def _make_finished_session(self, total_cost):
        return Session.objects.create(
            device=self.device, session_type='free',
            started_at=timezone.now(), ended_at=timezone.now(),
            status='finished', total_cost=total_cost,
        )

    def _make_free_tab(self, qty=1):
        return Sale.objects.create(
            product=self.product, quantity=qty, unit_price=10000,
            total_price=10000 * qty, payment_type='account',
            customer=None, session=None,
        )

    def test_cash_pays_sessions_and_tabs(self):
        s = self._make_finished_session(100000)
        sale = self._make_free_tab()
        resp = self.client.post('/pay/bulk/unified/', {
            'session_ids[]': [s.pk],
            'sale_ids[]': [sale.pk],
            'pay_type': 'cash',
        })
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        s.refresh_from_db()
        sale.refresh_from_db()
        self.assertTrue(s.is_fully_paid)
        self.assertEqual(sale.payment_type, 'cash')
        self.assertEqual(Payment.objects.filter(payment_type='cash').count(), 2)

    def test_account_deducts_combined_balance(self):
        customer = Customer.objects.create(name='رضا', balance=0)
        s = self._make_finished_session(100000)
        sale = self._make_free_tab(qty=2)   # 20 000
        self.client.post('/pay/bulk/unified/', {
            'session_ids[]': [s.pk],
            'sale_ids[]': [sale.pk],
            'pay_type': 'account',
            'customer_id': customer.pk,
        })
        customer.refresh_from_db()
        self.assertEqual(customer.balance, -120000)
        self.assertEqual(Payment.objects.filter(payment_type='account_debit').count(), 2)

    def test_empty_ids_redirects_with_error(self):
        resp = self.client.post('/pay/bulk/unified/', {'pay_type': 'cash'})
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.count(), 0)

    def test_account_without_customer_returns_error(self):
        s = self._make_finished_session(50000)
        resp = self.client.post('/pay/bulk/unified/', {
            'session_ids[]': [s.pk],
            'pay_type': 'account',
        })
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.count(), 0)

    def test_sessions_only_works(self):
        s1 = self._make_finished_session(60000)
        s2 = self._make_finished_session(40000)
        self.client.post('/pay/bulk/unified/', {
            'session_ids[]': [s1.pk, s2.pk],
            'pay_type': 'cash',
        })
        self.assertTrue(Session.objects.get(pk=s1.pk).is_fully_paid)
        self.assertTrue(Session.objects.get(pk=s2.pk).is_fully_paid)

    def test_tabs_only_works(self):
        sale1 = self._make_free_tab()
        sale2 = self._make_free_tab(qty=3)
        self.client.post('/pay/bulk/unified/', {
            'sale_ids[]': [sale1.pk, sale2.pk],
            'pay_type': 'cash',
        })
        self.assertEqual(Sale.objects.filter(payment_type='cash').count(), 2)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python manage.py test core.tests.UnifiedPayBulkTest -v 2
```

Expected: FAIL — `404 != 302` because the URL doesn't exist yet.

- [ ] **Step 3: Add the view to core/views.py**

Add the following function at the end of `core/views.py`, after `sessions_pay_bulk` (around line 651):

```python
@login_required
def unified_pay_bulk(request):
    """Pay a mixed selection of unpaid sessions and free-tab sales in one action."""
    if request.method != 'POST':
        return redirect('dashboard')

    session_ids = request.POST.getlist('session_ids[]')
    sale_ids    = request.POST.getlist('sale_ids[]')
    pay_type    = request.POST.get('pay_type', 'cash')
    customer_id = request.POST.get('customer_id') or None
    note        = request.POST.get('notes', '').strip()

    if not session_ids and not sale_ids:
        messages.error(request, 'هیچ موردی انتخاب نشده.')
        return redirect('dashboard')

    sessions = []
    if session_ids:
        sessions = [
            s for s in Session.objects.filter(pk__in=session_ids, status='finished')
                              .select_related('device')
                              .prefetch_related('payments', 'sales')
            if not s.is_fully_paid
        ]

    sales = []
    if sale_ids:
        sales = list(Sale.objects.filter(
            pk__in=sale_ids,
            payment_type='account',
            customer__isnull=True,
            session__isnull=True,
        ).select_related('product'))

    if not sessions and not sales:
        messages.error(request, 'موردی یافت نشد.')
        return redirect('dashboard')

    parts = []
    if sessions:
        parts.append('سشن: ' + '، '.join(s.device.name for s in sessions))
    if sales:
        parts.append('تب: ' + ' | '.join(f'{s.product.name} ×{s.quantity}' for s in sales))
    base_note = 'پرداخت یکجا — ' + ' / '.join(parts)
    if note:
        base_note += f' | {note}'

    session_total = sum(s.remaining_amount for s in sessions)
    sales_total   = sum(s.total_price for s in sales)
    total         = session_total + sales_total

    if pay_type == 'account' and not customer_id:
        messages.error(request, 'برای پرداخت حساب، مشتری را انتخاب کنید.')
        return redirect('dashboard')

    if pay_type == 'account' and customer_id:
        customer = get_object_or_404(Customer, pk=customer_id)
        ok, reason = customer.can_add_debt(total)
        if not ok:
            messages.error(request, f'حساب {customer.name} مسدود است: {reason}')
            return redirect('dashboard')
        for s in sessions:
            amt = s.remaining_amount
            customer.balance -= amt
            Payment.objects.create(
                session=s, customer=customer, amount=amt,
                payment_type='account_debit', notes=base_note,
            )
        for sale in sales:
            customer.balance -= sale.total_price
            sale.payment_type = 'account'
            sale.customer = customer
            sale.save()
            Payment.objects.create(
                amount=sale.total_price, payment_type='account_debit',
                customer=customer, notes=base_note,
            )
        customer.save()
        messages.success(request,
            f'پرداخت یکجا به حساب {customer.name} ثبت شد — {total:,} تومان')
    else:
        for s in sessions:
            Payment.objects.create(
                session=s, amount=s.remaining_amount,
                payment_type='cash', notes=base_note,
            )
        for sale in sales:
            sale.payment_type = 'cash'
            sale.save()
            Payment.objects.create(
                amount=sale.total_price, payment_type='cash', notes=base_note,
            )
        messages.success(request, f'پرداخت یکجا نقد ثبت شد — {total:,} تومان')

    return redirect('dashboard')
```

- [ ] **Step 4: Add URL to config/urls.py**

In `config/urls.py`, add the new route after the `sessions/bulk-pay/` line (around line 24):

```python
    path('sessions/bulk-pay/',        views.sessions_pay_bulk,  name='sessions_pay_bulk'),
    path('pay/bulk/unified/',         views.unified_pay_bulk,   name='unified_pay_bulk'),
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
python manage.py test core.tests.UnifiedPayBulkTest -v 2
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Run full test suite**

```bash
python manage.py test core -v 2
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add core/views.py config/urls.py core/tests.py
git commit -m "feat: add unified_pay_bulk endpoint for combined session+tab payment"
```

---

## Task 7: Add unified floating pay bar and modal to dashboard

**Files:**
- Modify: `templates/dashboard.html`

**What this does:** Adds a sticky bottom bar that appears when any session or tab checkbox is checked. Shows combined count and total across both sections. Clicking "پرداخت یکجا" opens a modal that submits to `/pay/bulk/unified/`. Adds `updateUnifiedBar()` calls to the existing selection-tracking functions.

- [ ] **Step 1: Add the floating bar HTML**

In `templates/dashboard.html`, find the `{% block modals %}` tag (around line 231). Add the floating bar and unified modal BEFORE that tag (i.e., inside `{% block content %}`, right before `{% endblock %}`):

```html
<!-- ── Unified floating pay bar ── -->
<div id="unifiedPayBar" style="display:none;position:fixed;bottom:0;right:0;left:0;z-index:1050;padding:.75rem 1.25rem;background:rgba(12,12,22,.96);backdrop-filter:blur(12px);border-top:1px solid rgba(245,158,11,.35);box-shadow:0 -4px 28px rgba(0,0,0,.55)">
  <div class="d-flex align-items-center justify-content-between gap-3 flex-wrap">
    <div>
      <div style="font-size:.72rem;color:var(--text-3)" id="unifiedBarCount"></div>
      <div class="num fw-bold" id="unifiedBarTotal" style="color:var(--neon-gold);font-size:1.15rem"></div>
    </div>
    <div class="d-flex gap-2">
      <button class="btn btn-outline-secondary btn-sm" onclick="clearAllSelections()">
        <i class="bi bi-x-lg me-1"></i>انصراف
      </button>
      <button class="btn btn-warning fw-bold btn-sm px-4"
              data-bs-toggle="modal" data-bs-target="#unifiedPayModal"
              onclick="prepareUnifiedModal()">
        <i class="bi bi-wallet2 me-1"></i>پرداخت یکجا
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add unified pay modal to {% block modals %}**

Inside `{% block modals %}` (which already contains several modals), add this modal after the last existing modal and before `{% endblock %}`:

```html
<!-- ══ UNIFIED PAY MODAL ══ -->
<div class="modal fade" id="unifiedPayModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content" style="border-color:rgba(245,158,11,.3)">
      <div class="modal-header" style="border-color:rgba(245,158,11,.2)">
        <h5 class="modal-title" style="color:var(--neon-gold)">
          <i class="bi bi-wallet2 me-2"></i>پرداخت یکجا
        </h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <form method="post" action="{% url 'unified_pay_bulk' %}" id="unifiedPayForm">
        {% csrf_token %}
        <div id="unifiedPayInputs"></div>
        <div class="modal-body">
          <div class="p-3 rounded-3 mb-3" style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2)">
            <div style="font-size:.75rem;color:var(--text-3);margin-bottom:.25rem" id="unifiedModalCount"></div>
            <div class="num" id="unifiedModalTotal" style="font-size:1.4rem;font-weight:700;color:var(--neon-gold)"></div>
          </div>
          <div id="unifiedModalItems" class="mb-3" style="font-size:.78rem;color:var(--text-2);max-height:120px;overflow-y:auto"></div>

          <div class="mb-3">
            <label class="form-label">روش پرداخت</label>
            <select name="pay_type" id="unifiedPayType" class="form-select" onchange="toggleUnifiedCustomer()">
              <option value="cash">💵 نقد</option>
              <option value="account">🏦 به حساب مشتری</option>
            </select>
          </div>
          <div id="unifiedCustomerWrap" class="mb-3" style="display:none">
            <label class="form-label">مشتری</label>
            <select name="customer_id" id="unifiedCustomerSelect" class="form-select">
              <option value="">— انتخاب مشتری —</option>
              {% for c in customers_for_modal %}
              <option value="{{ c.id }}">{{ c.name }}{% if c.balance < 0 %} ⚠{% endif %}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mb-1">
            <label class="form-label">یادداشت (اختیاری)</label>
            <input type="text" name="notes" class="form-control form-control-sm" placeholder="توضیح…">
          </div>
        </div>
        <div class="modal-footer" style="border-color:rgba(245,158,11,.2)">
          <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">انصراف</button>
          <button type="submit" class="btn btn-warning fw-bold btn-sm px-4">ثبت پرداخت</button>
        </div>
      </form>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add updateUnifiedBar() call to existing updateSessionSelection()**

Find `updateSessionSelection()` in `{% block extra_js %}`. It currently ends with the highlight loop:

```javascript
  // highlight selected cards
  document.querySelectorAll('.unpaid-session-card').forEach(card => {
    const cb = card.querySelector('.session-checkbox');
    card.style.borderColor = cb?.checked ? 'rgba(245,158,11,.7)' : 'rgba(245,158,11,.25)';
    card.style.background  = cb?.checked ? 'rgba(245,158,11,.12)' : 'rgba(255,255,255,.04)';
  });
}
```

Replace that closing section with:

```javascript
  // highlight selected cards
  document.querySelectorAll('.unpaid-session-card').forEach(card => {
    const cb = card.querySelector('.session-checkbox');
    card.style.borderColor = cb?.checked ? 'rgba(245,158,11,.7)' : 'rgba(245,158,11,.25)';
    card.style.background  = cb?.checked ? 'rgba(245,158,11,.12)' : 'rgba(255,255,255,.04)';
  });
  updateUnifiedBar();
}
```

- [ ] **Step 4: Add updateUnifiedBar() call to existing updateTabSelection()**

Find `updateTabSelection()`. It currently ends with:

```javascript
  // highlight selected cards
  document.querySelectorAll('.pending-tab-card').forEach(card => {
    const cb = card.querySelector('.tab-checkbox');
    card.style.borderColor = cb?.checked ? 'rgba(245,158,11,.7)' : 'rgba(251,191,36,.2)';
    card.style.background  = cb?.checked ? 'rgba(245,158,11,.1)' : 'rgba(255,255,255,.04)';
  });
}
```

Replace with:

```javascript
  // highlight selected cards
  document.querySelectorAll('.pending-tab-card').forEach(card => {
    const cb = card.querySelector('.tab-checkbox');
    card.style.borderColor = cb?.checked ? 'rgba(245,158,11,.7)' : 'rgba(251,191,36,.2)';
    card.style.background  = cb?.checked ? 'rgba(245,158,11,.1)' : 'rgba(255,255,255,.04)';
  });
  updateUnifiedBar();
}
```

- [ ] **Step 5: Add the unified bar JS functions**

At the end of `{% block extra_js %}`, just before the closing `</script>` tag, add:

```javascript
// ── UNIFIED PAY BAR ──────────────────────────────────────────────────────────
function updateUnifiedBar() {
  const sCbs  = document.querySelectorAll('.session-checkbox:checked');
  const tCbs  = document.querySelectorAll('.tab-checkbox:checked');
  const count = sCbs.length + tCbs.length;
  let total = 0;
  sCbs.forEach(cb => { total += parseInt(cb.dataset.amount || 0); });
  tCbs.forEach(cb => { total += parseInt(cb.dataset.amount || 0); });

  const bar = document.getElementById('unifiedPayBar');
  if (!bar) return;
  bar.style.display = count > 0 ? 'block' : 'none';
  document.body.style.paddingBottom = count > 0 ? '72px' : '';
  document.getElementById('unifiedBarCount').textContent  = count + ' مورد انتخاب‌شده';
  document.getElementById('unifiedBarTotal').textContent  = total.toLocaleString('fa-IR') + ' تومان';
}

function clearAllSelections() {
  document.querySelectorAll('.session-checkbox, .tab-checkbox').forEach(cb => { cb.checked = false; });
  // These two already call updateUnifiedBar() at their end
  updateSessionSelection();
  updateTabSelection();
}

function prepareUnifiedModal() {
  const sCbs = document.querySelectorAll('.session-checkbox:checked');
  const tCbs = document.querySelectorAll('.tab-checkbox:checked');
  const inputsDiv = document.getElementById('unifiedPayInputs');
  inputsDiv.innerHTML = '';
  let total = 0, items = [];

  sCbs.forEach(cb => {
    const inp = document.createElement('input');
    inp.type = 'hidden'; inp.name = 'session_ids[]'; inp.value = cb.dataset.sessionId;
    inputsDiv.appendChild(inp);
    total += parseInt(cb.dataset.amount || 0);
    items.push(cb.dataset.name);
  });
  tCbs.forEach(cb => {
    const inp = document.createElement('input');
    inp.type = 'hidden'; inp.name = 'sale_ids[]'; inp.value = cb.dataset.saleId;
    inputsDiv.appendChild(inp);
    total += parseInt(cb.dataset.amount || 0);
    items.push(cb.dataset.name);
  });

  document.getElementById('unifiedModalCount').textContent = (sCbs.length + tCbs.length) + ' مورد';
  document.getElementById('unifiedModalTotal').textContent = total.toLocaleString('fa-IR') + ' تومان';
  document.getElementById('unifiedModalItems').innerHTML =
    items.map(n => `<span class="badge-ghost" style="font-size:.72rem;margin:2px">${n}</span>`).join('');
  document.getElementById('unifiedPayType').value = 'cash';
  document.getElementById('unifiedCustomerWrap').style.display = 'none';
}

let unifiedCustomerTS = null;
document.getElementById('unifiedPayModal')?.addEventListener('shown.bs.modal', () => {
  if (!unifiedCustomerTS) {
    const el = document.getElementById('unifiedCustomerSelect');
    if (el) unifiedCustomerTS = new TomSelect(el, { create: false, maxOptions: 200 });
  }
});
function toggleUnifiedCustomer() {
  const show = document.getElementById('unifiedPayType').value === 'account';
  document.getElementById('unifiedCustomerWrap').style.display = show ? 'block' : 'none';
}
```

- [ ] **Step 6: Run full tests**

```bash
python manage.py test core -v 2
```

Expected: all tests PASS.

- [ ] **Step 7: Manual end-to-end test**

Start the server. On the dashboard:
1. Check one unpaid session checkbox AND one free-tab checkbox simultaneously.
2. Verify the floating bar appears at the bottom showing the combined count and total.
3. Click "پرداخت یکجا" — modal opens with correct total.
4. Submit as cash — redirects to dashboard, both items gone, success message shown.
5. Click "انصراف" in the bar — all checkboxes clear, bar hides.

```bash
python manage.py runserver
```

- [ ] **Step 8: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: add unified floating pay bar for combined session+tab bulk payment"
```
