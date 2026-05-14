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
