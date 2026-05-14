# Bulk Session Payment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff select multiple unpaid sessions on the dashboard and settle all of them in one form submission (cash or customer account debit).

**Architecture:** One new view `sessions_pay_bulk` creates a `Payment` record per selected session (so each session's `paid_amount` property works correctly), all in a single POST. The dashboard UI mirrors the existing shop-tabs bulk-pay pattern: checkboxes on each session card, a header button showing count + total, and a modal with payment type + optional customer.

**Tech Stack:** Django 4.x, Bootstrap RTL, vanilla JS, TomSelect

---

## File Map

| File | Change |
|---|---|
| `config/urls.py` | Add one URL line for `sessions_pay_bulk` |
| `core/views.py` | Add `sessions_pay_bulk` view after `sales_pay_bulk` |
| `templates/dashboard.html` | Add checkboxes to session cards, header controls, modal, JS |

---

## Task 1: Add URL route

**Files:**
- Modify: `config/urls.py:23` (after `sessions/history/` line)

- [ ] **Step 1: Add the URL**

In `config/urls.py`, after line 23 (`path('sessions/history/', ...)`), add:

```python
path('sessions/bulk-pay/',         views.sessions_pay_bulk, name='sessions_pay_bulk'),
```

The sessions block should now look like:

```python
# Sessions
path('session/start/',            views.session_start,     name='session_start'),
path('session/<int:pk>/',         views.session_detail,    name='session_detail'),
path('session/<int:pk>/end/',     views.session_end,       name='session_end'),
path('session/<int:pk>/cancel/',  views.session_cancel,    name='session_cancel'),
path('session/<int:pk>/pay/',     views.session_pay,       name='session_pay'),
path('session/<int:pk>/status/',  views.session_status,    name='session_status'),
path('session/<int:pk>/update/',  views.session_update,    name='session_update'),
path('sessions/history/',         views.sessions_history,  name='sessions_history'),
path('sessions/bulk-pay/',        views.sessions_pay_bulk, name='sessions_pay_bulk'),
```

- [ ] **Step 2: Verify Django can resolve the URL**

```bash
cd /home/sm/WorkSpace/gamenet-management-system
source .venv/bin/activate
python manage.py shell -c "from django.urls import reverse; print(reverse('sessions_pay_bulk'))"
```

Expected output (will fail until view exists — that's fine, move on):
```
django.urls.exceptions.NoReverseMatch ...
```
This just confirms the URL file is valid Python. If you get a SyntaxError, fix it before continuing.

- [ ] **Step 3: Commit**

```bash
git add config/urls.py
git commit -m "feat: add sessions/bulk-pay/ URL route"
```

---

## Task 2: Add `sessions_pay_bulk` view

**Files:**
- Modify: `core/views.py` — add after `sales_pay_bulk` (currently ends at line 585)

- [ ] **Step 1: Write a failing test**

Add to `core/tests.py`:

```python
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Device, Session, Customer, Payment


class BulkSessionPayTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('staff', password='pass')
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            name='PS5-1', device_type='ps5', price_per_hour=60000
        )

    def _make_finished_session(self, total_cost):
        s = Session.objects.create(
            device=self.device,
            session_type='free',
            started_at=timezone.now(),
            ended_at=timezone.now(),
            status='finished',
            total_cost=total_cost,
        )
        return s

    def test_cash_bulk_pay_clears_sessions(self):
        s1 = self._make_finished_session(100000)
        s2 = self._make_finished_session(200000)
        resp = self.client.post('/sessions/bulk-pay/', {
            'session_ids[]': [s1.pk, s2.pk],
            'pay_type': 'cash',
            'notes': 'تست',
        })
        self.assertRedirects(resp, '/')
        s1.refresh_from_db(); s2.refresh_from_db()
        self.assertTrue(s1.is_fully_paid)
        self.assertTrue(s2.is_fully_paid)
        self.assertEqual(Payment.objects.filter(payment_type='cash').count(), 2)

    def test_account_bulk_pay_deducts_balance(self):
        customer = Customer.objects.create(name='علی', balance=0)
        s1 = self._make_finished_session(80000)
        s2 = self._make_finished_session(120000)
        self.client.post('/sessions/bulk-pay/', {
            'session_ids[]': [s1.pk, s2.pk],
            'pay_type': 'account',
            'customer_id': customer.pk,
        })
        customer.refresh_from_db()
        self.assertEqual(customer.balance, -200000)
        self.assertEqual(Payment.objects.filter(payment_type='account_debit').count(), 2)

    def test_skips_already_paid_sessions(self):
        s1 = self._make_finished_session(50000)
        Payment.objects.create(session=s1, amount=50000, payment_type='cash')
        s2 = self._make_finished_session(50000)
        self.client.post('/sessions/bulk-pay/', {
            'session_ids[]': [s1.pk, s2.pk],
            'pay_type': 'cash',
        })
        # s1 was already paid — only one new payment created
        self.assertEqual(Payment.objects.filter(payment_type='cash').count(), 2)  # 1 existing + 1 new

    def test_empty_ids_redirects_with_error(self):
        resp = self.client.post('/sessions/bulk-pay/', {'pay_type': 'cash'})
        self.assertRedirects(resp, '/')

    def test_account_blocked_by_debt_limit(self):
        customer = Customer.objects.create(name='سارا', balance=0, debt_limit=50000)
        s1 = self._make_finished_session(100000)
        resp = self.client.post('/sessions/bulk-pay/', {
            'session_ids[]': [s1.pk],
            'pay_type': 'account',
            'customer_id': customer.pk,
        })
        self.assertRedirects(resp, '/')
        customer.refresh_from_db()
        self.assertEqual(customer.balance, 0)  # unchanged
        self.assertEqual(Payment.objects.count(), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python manage.py test core.tests.BulkSessionPayTest -v 2
```

Expected: All 5 tests FAIL — `sessions_pay_bulk` doesn't exist yet.

- [ ] **Step 3: Add the view to `core/views.py`**

Find the end of `sales_pay_bulk` (the line `return redirect('dashboard')` after the sales bulk pay logic, around line 585). Add the new view immediately after:

```python
@login_required
def sessions_pay_bulk(request):
    """Bulk-settle multiple finished sessions in one action."""
    if request.method != 'POST':
        return redirect('dashboard')

    session_ids = request.POST.getlist('session_ids[]')
    pay_type    = request.POST.get('pay_type', 'cash')
    customer_id = request.POST.get('customer_id') or None
    note        = request.POST.get('notes', '').strip()

    if not session_ids:
        messages.error(request, 'هیچ سشنی انتخاب نشده.')
        return redirect('dashboard')

    sessions = [
        s for s in Session.objects.filter(pk__in=session_ids, status='finished')
                          .select_related('device')
        if not s.is_fully_paid
    ]

    if not sessions:
        messages.error(request, 'سشن پرداخت‌نشده‌ای یافت نشد.')
        return redirect('dashboard')

    device_names = '، '.join(s.device.name for s in sessions)
    base_note    = f'تسویه گروهی: {device_names}'
    if note:
        base_note += f' | {note}'

    if pay_type == 'account' and customer_id:
        customer  = get_object_or_404(Customer, pk=customer_id)
        total     = sum(s.remaining_amount for s in sessions)
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
        customer.save()
        messages.success(request,
            f'{len(sessions)} سشن به حساب {customer.name} تسویه شد.')
    else:
        for s in sessions:
            Payment.objects.create(
                session=s, amount=s.remaining_amount,
                payment_type='cash', notes=base_note,
            )
        total = sum(s.remaining_amount for s in sessions)
        messages.success(request,
            f'{len(sessions)} سشن نقد تسویه شد — {total:,} تومان')

    return redirect('dashboard')
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python manage.py test core.tests.BulkSessionPayTest -v 2
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/views.py core/tests.py
git commit -m "feat: add sessions_pay_bulk view with tests"
```

---

## Task 3: Update dashboard HTML — session cards + header controls

**Files:**
- Modify: `templates/dashboard.html` lines 105–130

- [ ] **Step 1: Replace the unpaid sessions card block**

Find the block starting at line 105 (`{% if unpaid_sessions %}`). Replace the entire block (lines 105–130) with:

```html
{% if unpaid_sessions %}
<div class="card mb-4 fade-in" style="border-color:rgba(245,158,11,0.3);background:rgba(245,158,11,.04)">
  <div style="position:absolute;top:0;right:0;left:0;height:2px;background:linear-gradient(90deg,#d97706,#fbbf24);border-radius:var(--radius) var(--radius) 0 0"></div>
  <div class="p-3">
    <div class="d-flex align-items-center gap-2 mb-3 flex-wrap">
      <i class="bi bi-hourglass-split dot-pulse" style="color:var(--neon-gold)"></i>
      <strong>پرداخت‌نشده</strong>
      <span class="badge-ghost" style="font-size:.7rem">{{ unpaid_sessions|length }} مورد</span>
      <div class="ms-auto d-flex align-items-center gap-2">
        <label class="d-flex align-items-center gap-1 mb-0" style="cursor:pointer;font-size:.8rem;color:var(--text-3)">
          <input type="checkbox" id="selectAllSessions" onchange="toggleSelectAllSessions(this)" class="form-check-input">
          همه
        </label>
        <button class="btn btn-warning btn-sm fw-bold" id="bulkSessionPayBtn"
                data-bs-toggle="modal" data-bs-target="#bulkSessionPayModal"
                onclick="prepareBulkSessionModal()" disabled>
          <i class="bi bi-wallet2 me-1"></i><span id="bulkSessionPayBtnText">پرداخت انتخاب‌شده</span>
        </button>
      </div>
    </div>
    <div class="row g-2">
      {% for s in unpaid_sessions %}
      <div class="col-sm-6 col-md-4 col-lg-3">
        <div class="p-2 rounded-3 d-flex flex-column gap-1 unpaid-session-card"
             style="background:rgba(255,255,255,.04);border:1px solid rgba(245,158,11,.25);transition:border-color .15s">
          <div class="d-flex align-items-start gap-2">
            <input type="checkbox" class="session-checkbox form-check-input mt-1 flex-shrink-0"
                   id="ses-check-{{ s.pk }}"
                   data-session-id="{{ s.pk }}"
                   data-amount="{{ s.remaining_amount }}"
                   data-name="{{ s.device.name|escapejs }}"
                   onchange="updateSessionSelection()">
            <div class="flex-grow-1" onclick="document.getElementById('ses-check-{{ s.pk }}').click()" style="cursor:pointer">
              <div class="d-flex justify-content-between align-items-center">
                <span style="font-size:.82rem;font-weight:600;color:var(--text)">{{ s.device.name }}</span>
                <span class="num" style="color:var(--neon-gold);font-size:.85rem">{{ s.remaining_amount|intcomma }} تومان</span>
              </div>
              {% if s.notes %}<div style="font-size:.72rem;color:var(--neon-cyan)"><i class="bi bi-chat-left-text me-1"></i>{{ s.notes }}</div>{% endif %}
            </div>
          </div>
          <a href="{% url 'session_pay' s.pk %}" class="btn btn-warning btn-sm w-100 fw-bold mt-1">💳 پرداخت</a>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
</div>
{% endif %}
```

- [ ] **Step 2: Verify the template renders without errors**

```bash
python manage.py check --deploy 2>&1 | grep -i error || echo "OK"
```

Expected: no template errors (Django's `check` catches template syntax issues).

- [ ] **Step 3: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: add checkboxes and bulk-pay button to unpaid sessions card"
```

---

## Task 4: Add `bulkSessionPayModal` to dashboard

**Files:**
- Modify: `templates/dashboard.html` — add modal after `bulkTabPayModal` (after line 470, before `<!-- ══ CANCEL SESSION MODAL ══ -->`)

- [ ] **Step 1: Add the modal HTML**

Find the comment `<!-- ══ CANCEL SESSION MODAL ══ -->` (around line 472). Insert the following block immediately before it:

```html
<!-- ══ BULK SESSION PAY MODAL ══ -->
<div class="modal fade" id="bulkSessionPayModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content" style="border-color:rgba(245,158,11,.3)">
      <div class="modal-header" style="border-color:rgba(245,158,11,.2)">
        <h5 class="modal-title" style="color:var(--neon-gold)">
          <i class="bi bi-wallet2 me-2"></i>پرداخت سشن‌های انتخاب‌شده
        </h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <form method="post" action="{% url 'sessions_pay_bulk' %}" id="bulkSessionPayForm">
        {% csrf_token %}
        <div id="bulkSessionInputs"></div>
        <div class="modal-body">
          <div class="p-3 rounded-3 mb-3" style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2)">
            <div style="font-size:.75rem;color:var(--text-3);margin-bottom:.25rem">جمع انتخاب‌شده</div>
            <div class="num" id="bulkSessionModalTotal" style="font-size:1.4rem;font-weight:700;color:var(--neon-gold)">0 تومان</div>
            <div style="font-size:.75rem;color:var(--text-3);margin-top:.35rem" id="bulkSessionModalCount"></div>
          </div>
          <div id="bulkSessionModalItems" class="mb-3" style="font-size:.78rem;color:var(--text-2);max-height:120px;overflow-y:auto"></div>

          <div class="mb-3">
            <label class="form-label">روش پرداخت</label>
            <select name="pay_type" id="bulkSessionPayType" class="form-select" onchange="toggleBulkSessionCustomer()">
              <option value="cash">💵 نقد</option>
              <option value="account">🏦 به حساب مشتری</option>
            </select>
          </div>

          <div id="bulkSessionCustomerWrap" class="mb-3" style="display:none">
            <label class="form-label">مشتری</label>
            <select name="customer_id" id="bulkSessionCustomerSelect" class="form-select">
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

- [ ] **Step 2: Verify template syntax**

```bash
python manage.py check 2>&1 | grep -i error || echo "OK"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: add bulkSessionPayModal to dashboard"
```

---

## Task 5: Add JavaScript for session selection

**Files:**
- Modify: `templates/dashboard.html` — add JS after the `toggleBulkCustomer` function (around line 795, before the `// ── SORTABLE` comment)

- [ ] **Step 1: Add the JS block**

Find the comment `// ── SORTABLE — save card order to server ──` (around line 797). Insert the following block immediately before it:

```javascript
// ── BULK SESSION PAY ───────────────────────────────────────────────────────
function updateSessionSelection() {
  const checked = document.querySelectorAll('.session-checkbox:checked');
  const all     = document.querySelectorAll('.session-checkbox');
  let total = 0;
  checked.forEach(cb => { total += parseInt(cb.dataset.amount || 0); });
  const count = checked.length;

  const btn = document.getElementById('bulkSessionPayBtn');
  if (btn) {
    btn.disabled = count === 0;
    document.getElementById('bulkSessionPayBtnText').textContent =
      count > 0 ? `پرداخت ${count} مورد — ${total.toLocaleString('fa-IR')} تومان` : 'پرداخت انتخاب‌شده';
  }

  const allCb = document.getElementById('selectAllSessions');
  if (allCb) {
    allCb.checked       = all.length > 0 && count === all.length;
    allCb.indeterminate = count > 0 && count < all.length;
  }

  document.querySelectorAll('.unpaid-session-card').forEach(card => {
    const cb = card.querySelector('.session-checkbox');
    card.style.borderColor = cb?.checked ? 'rgba(245,158,11,.7)' : 'rgba(245,158,11,.25)';
    card.style.background  = cb?.checked ? 'rgba(245,158,11,.1)' : 'rgba(255,255,255,.04)';
  });
}

function toggleSelectAllSessions(allCb) {
  document.querySelectorAll('.session-checkbox').forEach(cb => { cb.checked = allCb.checked; });
  updateSessionSelection();
}

function prepareBulkSessionModal() {
  const checked   = document.querySelectorAll('.session-checkbox:checked');
  const inputsDiv = document.getElementById('bulkSessionInputs');
  inputsDiv.innerHTML = '';
  let total = 0, items = [];
  checked.forEach(cb => {
    const inp = document.createElement('input');
    inp.type = 'hidden'; inp.name = 'session_ids[]'; inp.value = cb.dataset.sessionId;
    inputsDiv.appendChild(inp);
    total += parseInt(cb.dataset.amount || 0);
    items.push(cb.dataset.name);
  });
  document.getElementById('bulkSessionModalTotal').textContent = total.toLocaleString('fa-IR') + ' تومان';
  document.getElementById('bulkSessionModalCount').textContent = checked.length + ' سشن';
  document.getElementById('bulkSessionModalItems').innerHTML =
    items.map(n => `<span class="badge-ghost" style="font-size:.72rem;margin:2px">${n}</span>`).join('');
  document.getElementById('bulkSessionPayType').value = 'cash';
  document.getElementById('bulkSessionCustomerWrap').style.display = 'none';
}

let bulkSessionCustomerTS = null;
document.getElementById('bulkSessionPayModal').addEventListener('shown.bs.modal', () => {
  if (!bulkSessionCustomerTS) {
    const el = document.getElementById('bulkSessionCustomerSelect');
    if (el) bulkSessionCustomerTS = new TomSelect(el, { create: false, maxOptions: 200 });
  }
});

function toggleBulkSessionCustomer() {
  const show = document.getElementById('bulkSessionPayType').value === 'account';
  document.getElementById('bulkSessionCustomerWrap').style.display = show ? 'block' : 'none';
}
```

- [ ] **Step 2: Run all tests**

```bash
python manage.py test core -v 2
```

Expected: All tests PASS.

- [ ] **Step 3: Smoke-test in browser**

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. If there are no unpaid sessions, create one:
1. Click a free device → start a session → immediately end it → choose "leave" on pay page to leave it unpaid.
2. Back on dashboard, confirm the unpaid sessions card shows a checkbox on the session card and the "همه" + bulk pay button in the header.
3. Check one session → button should enable and show `پرداخت 1 مورد — X تومان`.
4. Click the bulk pay button → modal opens with the session listed and total shown.
5. Choose "نقد" → submit → confirm redirect to dashboard with success toast and the session gone from the unpaid list.
6. Repeat with "به حساب مشتری" — confirm customer balance decreases.

- [ ] **Step 4: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: add JS for bulk session selection and pay modal"
```
