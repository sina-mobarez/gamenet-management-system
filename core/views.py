from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum

from .models import (
    Device, Customer, Session,
    Product, Sale, Payment
)


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        user = authenticate(request,
                            username=request.POST['username'],
                            password=request.POST['password'])
        if user:
            login(request, user)
            return redirect('dashboard')
        messages.error(request, 'نام کاربری یا رمز اشتباه است.')
    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ── Dashboard ─────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    devices = Device.objects.filter(is_active=True)
    low_stock = Product.objects.filter(is_active=True).extra(
        where=["stock <= low_stock_threshold"]
    )

    today = timezone.localdate()
    today_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time())
    )
    today_cash = (
        Payment.objects.filter(created_at__gte=today_start, payment_type='cash')
        .aggregate(t=Sum('amount'))['t'] or 0
    )
    today_sales_cash = (
        Sale.objects.filter(sold_at__gte=today_start, payment_type='cash')
        .aggregate(t=Sum('total_price'))['t'] or 0
    )
    total_debt = abs(
        Customer.objects.filter(balance__lt=0)
        .aggregate(t=Sum('balance'))['t'] or 0
    )

    unpaid_sessions = [
        s for s in Session.objects.filter(status='finished')
                          .select_related('device').order_by('-ended_at')
        if not s.is_fully_paid
    ]

    return render(request, 'dashboard.html', {
        'devices': devices,
        'low_stock': low_stock,
        'today_cash': float(today_cash) + float(today_sales_cash),
        'total_debt': float(total_debt),
        'customers_for_modal': Customer.objects.all().order_by('name'),
        'unpaid_sessions': unpaid_sessions,
    })