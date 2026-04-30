from django.db import models
from django.utils import timezone


class Device(models.Model):
    DEVICE_TYPES = [
        ('billiard', 'Billiard'),
        ('snooker',  'Snooker'),
        ('ps4',      'PS4'),
        ('ps5',      'PS5'),
        ('airHocky', 'Air Hocky'),
        ('system', 'Custom System'),
        ('tennis', 'Tennis')
    ]
    name                   = models.CharField(max_length=100)
    device_type            = models.CharField(max_length=20, choices=DEVICE_TYPES)
    price_per_hour         = models.IntegerField(default=0)          # تومان/ساعت
    extra_controller_price = models.IntegerField(default=0)          # اضافه به نرخ ساعتی
    included_controllers   = models.PositiveIntegerField(default=2)
    is_active              = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.get_device_type_display()})"

    def effective_hourly_rate(self, extra_controllers=0):
        """Rate per hour including extra controller charge (added to hourly, not flat)."""
        return self.price_per_hour + (extra_controllers * self.extra_controller_price)

    @property
    def is_occupied(self):
        return self.sessions.filter(status='active').exists()

    @property
    def active_session(self):
        return self.sessions.filter(status='active').first()

    @property
    def unpaid_session(self):
        for s in self.sessions.filter(status='finished').order_by('-ended_at'):
            if not s.is_fully_paid:
                return s
        return None


class Customer(models.Model):
    name       = models.CharField(max_length=200)
    phone      = models.CharField(max_length=20, blank=True)
    balance    = models.IntegerField(default=0)   # negative = owes, positive = credit
    debt_limit = models.IntegerField(default=0)   # 0 = unlimited; positive = max allowed debt
    created_at = models.DateTimeField(auto_now_add=True)
    notes      = models.TextField(blank=True)

    def __str__(self):
        return self.name

    @property
    def has_debt(self):
        return self.balance < 0

    @property
    def debt_amount(self):
        return abs(self.balance) if self.balance < 0 else 0

    @property
    def is_suspended(self):
        """Account is suspended if debt_limit set and debt exceeds it."""
        if self.debt_limit <= 0:
            return False
        return self.debt_amount >= self.debt_limit

    def can_add_debt(self, amount):
        """Returns (ok: bool, reason: str)"""
        if self.debt_limit <= 0:
            return True, ''
        projected = self.debt_amount + amount
        if projected > self.debt_limit:
            return False, f'سقف بدهی ({self.debt_limit:,} تومان) تجاوز می‌کند.'
        return True, ''


class Session(models.Model):
    STATUS_CHOICES = [
        ('active',    'Active'),
        ('finished',  'Finished'),
        ('cancelled', 'Cancelled'),
    ]
    SESSION_TYPES = [
        ('free',  'آزاد'),
        ('timed', 'زمان‌دار'),
    ]

    device             = models.ForeignKey(Device, on_delete=models.PROTECT, related_name='sessions')
    started_at         = models.DateTimeField(default=timezone.now)
    ended_at           = models.DateTimeField(null=True, blank=True)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    session_type       = models.CharField(max_length=10, choices=SESSION_TYPES, default='free')
    duration_minutes   = models.PositiveIntegerField(null=True, blank=True)  # timed sessions only
    extra_controllers  = models.PositiveIntegerField(default=0)
    notes              = models.TextField(blank=True)
    total_cost         = models.IntegerField(null=True, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.device.name} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"

    def calculate_cost(self):
        end = self.ended_at or timezone.now()
        duration_seconds = (end - self.started_at).total_seconds()
        duration_hours = duration_seconds / 3600
        rate = self.device.effective_hourly_rate(self.extra_controllers)
        raw_cost = duration_hours * rate
        return int(round(raw_cost, -3))

    @property
    def duration_display(self):
        end = self.ended_at or timezone.now()
        seconds = int((end - self.started_at).total_seconds())
        h, r = divmod(seconds, 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def elapsed_seconds(self):
        end = self.ended_at or timezone.now()
        return int((end - self.started_at).total_seconds())

    @property
    def remaining_seconds(self):
        """For timed sessions: seconds left. Negative = overrun."""
        if self.session_type != 'timed' or not self.duration_minutes:
            return None
        limit = self.duration_minutes * 60
        return limit - self.elapsed_seconds

    @property
    def paid_amount(self):
        from django.db.models import Sum
        total = self.payments.aggregate(t=Sum('amount'))['t']
        return int(total) if total is not None else 0

    @property
    def remaining_amount(self):
        if self.total_cost is None:
            return 0
        return max(0, self.total_cost - self.paid_amount)

    @property
    def is_fully_paid(self):
        return self.remaining_amount <= 0


class SessionPlayer(models.Model):
    session     = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='players')
    customer    = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    player_name = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.display_name} @ {self.session}"

    @property
    def display_name(self):
        return self.customer.name if self.customer else self.player_name


class ProductCategory(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Product Categories"


class Product(models.Model):
    name                = models.CharField(max_length=200)
    category            = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL,
                                            null=True, blank=True)
    price               = models.IntegerField(default=0)
    stock               = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    is_active           = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self):
        return self.stock <= self.low_stock_threshold


class Sale(models.Model):
    PAYMENT_TYPES = [
        ('cash',    'Cash'),
        ('account', 'On Account'),
    ]
    product      = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity     = models.PositiveIntegerField(default=1)
    unit_price   = models.IntegerField(default=0)
    total_price  = models.IntegerField(default=0)
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPES, default='cash')
    customer     = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    session      = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='sales')
    notes        = models.TextField(blank=True)
    sold_at      = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        self.total_price = self.unit_price * self.quantity
        super().save(*args, **kwargs)


class Payment(models.Model):
    PAYMENT_TYPES = [
        ('cash',                'نقد'),
        ('account_debit',       'حساب'),
        ('account_settlement',  'تسویه'),
    ]
    session      = models.ForeignKey(Session, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='payments')
    customer     = models.ForeignKey(Customer, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='payments')
    amount       = models.IntegerField(default=0)
    payment_type = models.CharField(max_length=30, choices=PAYMENT_TYPES)
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.get_payment_type_display()} | {self.amount:,} | {self.created_at.strftime('%Y-%m-%d %H:%M')}"
