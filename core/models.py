from django.db import models
from django.utils import timezone
from decimal import Decimal


class Device(models.Model):
    DEVICE_TYPES = [
        ('billiard', 'Billiard'),
        ('snooker', 'Snooker'),
        ('ps4', 'PS4'),
        ('ps5', 'PS5'),
        ('system', 'Custom System')
    ]
    name = models.CharField(max_length=100)
    device_type = models.CharField(max_length=20, choices=DEVICE_TYPES)
    price_per_hour = models.DecimalField(max_digits=10, decimal_places=0)
    extra_controller_price = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    included_controllers = models.PositiveIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.get_device_type_display()})"

    @property
    def is_occupied(self):
        return self.sessions.filter(status='active').exists()

    @property
    def active_session(self):
        return self.sessions.filter(status='active').first()

    @property
    def unpaid_session(self):
        """Finished session that still has remaining balance"""
        for s in self.sessions.filter(status='finished').order_by('-ended_at'):
            if not s.is_fully_paid:
                return s
        return None


class Customer(models.Model):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    balance = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name

    @property
    def has_debt(self):
        return self.balance < 0

    @property
    def debt_amount(self):
        return abs(self.balance) if self.balance < 0 else Decimal('0')


class Session(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('finished', 'Finished'),
        ('cancelled', 'Cancelled'),
    ]
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name='sessions')
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    extra_controllers = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    total_cost = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.device.name} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"

    def calculate_cost(self):
        end = self.ended_at or timezone.now()
        duration_seconds = (end - self.started_at).total_seconds()
        duration_hours = Decimal(str(duration_seconds / 3600))
        base_cost = duration_hours * self.device.price_per_hour
        extra_cost = self.extra_controllers * self.device.extra_controller_price
        return round(base_cost + extra_cost, 2)

    @property
    def duration_display(self):
        end = self.ended_at or timezone.now()
        seconds = int((end - self.started_at).total_seconds())
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def elapsed_seconds(self):
        end = self.ended_at or timezone.now()
        return int((end - self.started_at).total_seconds())

    @property
    def paid_amount(self):
        """Sum of all payments already recorded for this session."""
        from django.db.models import Sum as DSum
        total = self.payments.aggregate(t=DSum('amount'))['t']
        return Decimal(str(total)) if total is not None else Decimal('0')

    @property
    def remaining_amount(self):
        if self.total_cost is None:
            return Decimal('0')
        cost = Decimal(str(self.total_cost))
        remaining = cost - self.paid_amount
        return max(Decimal('0'), remaining)

    @property
    def is_fully_paid(self):
        return self.remaining_amount <= Decimal('0.01')


class SessionPlayer(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='players')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
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
    name = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self):
        return self.stock <= self.low_stock_threshold


class Sale(models.Model):
    PAYMENT_TYPES = [
        ('cash', 'Cash'),
        ('account', 'On Account'),
    ]
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=0)
    total_price = models.DecimalField(max_digits=12, decimal_places=0)
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPES, default='cash')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    sold_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"

    def save(self, *args, **kwargs):
        self.total_price = self.unit_price * self.quantity
        super().save(*args, **kwargs)


class Payment(models.Model):
    """
    Every money movement is recorded here.
    - type 'cash'             : cash paid toward a session
    - type 'account_debit'   : amount charged to a customer account (adds to their debt)
    - type 'account_settlement': customer pays off their debt with cash
    """
    PAYMENT_TYPES = [
        ('cash', 'نقد / Cash'),
        ('account_debit', 'حساب / Account'),
        ('account_settlement', 'تسویه / Settlement'),
    ]
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='payments')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=0)
    payment_type = models.CharField(max_length=30, choices=PAYMENT_TYPES)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.get_payment_type_display()} | ${self.amount} | {self.created_at.strftime('%Y-%m-%d %H:%M')}"
