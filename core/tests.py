from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Device, Session, Customer, Payment, Sale, Product, ProductCategory


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
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
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
        self.assertRedirects(resp, '/', fetch_redirect_response=False)

    def test_account_blocked_by_debt_limit(self):
        customer = Customer.objects.create(name='سارا', balance=0, debt_limit=50000)
        s1 = self._make_finished_session(100000)
        resp = self.client.post('/sessions/bulk-pay/', {
            'session_ids[]': [s1.pk],
            'pay_type': 'account',
            'customer_id': customer.pk,
        })
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, 0)  # unchanged
        self.assertEqual(Payment.objects.count(), 0)

    def test_account_without_customer_returns_error(self):
        s1 = self._make_finished_session(50000)
        resp = self.client.post('/sessions/bulk-pay/', {
            'session_ids[]': [s1.pk],
            'pay_type': 'account',
            # no customer_id
        })
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.count(), 0)


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
        self.assertEqual(s.remaining_amount, 116000)

    def test_is_fully_paid_requires_cafe_tab_covered(self):
        s = self._make_finished_session(100000)
        self._add_tab(s)                 # 8 000 café tab
        Payment.objects.create(session=s, amount=100000, payment_type='cash')
        self.assertFalse(s.is_fully_paid)

    def test_is_fully_paid_true_when_all_covered(self):
        s = self._make_finished_session(100000)
        self._add_tab(s)                 # 8 000 café tab
        Payment.objects.create(session=s, amount=108000, payment_type='cash')
        s_fresh = Session.objects.get(pk=s.pk)
        self.assertTrue(s_fresh.is_fully_paid)


class UnifiedPayBulkTest(TestCase):
    def setUp(self):
        self.client.force_login(
            __import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model()
            .objects.create_user('op', password='pw')
        )
        self.device = Device.objects.create(
            name='PS5-X', device_type='ps5', price_per_hour=60000
        )
        cat = ProductCategory.objects.create(name='کافه')
        self.product = Product.objects.create(
            name='چای', price=5000, stock=50, category=cat
        )
        self.customer = Customer.objects.create(name='علی', balance=0)

    def _finished_session(self, cost):
        return Session.objects.create(
            device=self.device, session_type='free',
            started_at=timezone.now(), ended_at=timezone.now(),
            status='finished', total_cost=cost,
        )

    def _tab_sale(self, session):
        return Sale.objects.create(
            product=self.product, quantity=1, unit_price=5000,
            total_price=5000, payment_type='account',
            session=session, customer=None,
        )

    def _post(self, session_ids=None, sale_ids=None, payment_type='cash', customer_id=''):
        data = {
            'payment_type': payment_type,
            'customer_id': str(customer_id),
        }
        for sid in (session_ids or []):
            data.setdefault('session_ids', []).append(str(sid))
        for sid_id in (sale_ids or []):
            data.setdefault('sale_ids', []).append(str(sid_id))
        return self.client.post('/pay/bulk/unified/', data)

    def test_empty_selection_redirects_with_error(self):
        resp = self._post()
        self.assertRedirects(resp, '/', fetch_redirect_response=False)

    def test_cash_pays_session_and_tab_sale(self):
        s = self._finished_session(50000)
        sale = self._tab_sale(s)
        resp = self._post(session_ids=[s.pk], sale_ids=[sale.pk])
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.filter(session=s).count(), 1)
        sale.refresh_from_db()
        self.assertEqual(sale.payment_type, 'cash')

    def test_account_pays_and_deducts_customer_balance(self):
        self.customer.balance = 100000
        self.customer.save()
        s = self._finished_session(30000)
        resp = self._post(session_ids=[s.pk], payment_type='account', customer_id=self.customer.pk)
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, 70000)

    def test_account_without_customer_fails(self):
        s = self._finished_session(20000)
        resp = self._post(session_ids=[s.pk], payment_type='account', customer_id='')
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.count(), 0)

    def test_account_blocked_by_debt_limit(self):
        self.customer.balance = 0
        self.customer.debt_limit = 10000
        self.customer.save()
        s = self._finished_session(50000)
        resp = self._post(session_ids=[s.pk], payment_type='account', customer_id=self.customer.pk)
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.count(), 0)

    def test_already_paid_session_skipped(self):
        s = self._finished_session(20000)
        Payment.objects.create(session=s, amount=20000, payment_type='cash')
        resp = self._post(session_ids=[s.pk])
        self.assertRedirects(resp, '/', fetch_redirect_response=False)
        self.assertEqual(Payment.objects.filter(session=s).count(), 1)
