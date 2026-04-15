from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q, Sum
from decimal import Decimal, InvalidOperation

from .models import (
    Device, Customer, Session, SessionPlayer,
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


# ── Sessions ──────────────────────────────────────────────────────────────────

@login_required
def session_start(request):
    if request.method != 'POST':
        return redirect('dashboard')
    device = get_object_or_404(Device, id=request.POST.get('device_id'))
    if device.is_occupied:
        messages.error(request, 'این دستگاه در حال استفاده است.')
        return redirect('dashboard')

    session = Session.objects.create(
        device=device,
        extra_controllers=int(request.POST.get('extra_controllers', 0))
    )
    for cid in request.POST.getlist('player_ids'):
        if cid:
            SessionPlayer.objects.create(session=session, customer_id=cid)
    for name in request.POST.getlist('player_names'):
        if name.strip():
            SessionPlayer.objects.create(session=session, player_name=name.strip())

    messages.success(request, f'سشن شروع شد روی {device.name}.')
    return redirect('dashboard')


@login_required
def session_detail(request, pk):
    session = get_object_or_404(Session, pk=pk)
    current_cost = session.calculate_cost()
    return render(request, 'session_detail.html', {
        'session': session,
        'current_cost': current_cost,
    })


@login_required
def session_end(request, pk):
    session = get_object_or_404(Session, pk=pk, status='active')
    session.ended_at = timezone.now()
    session.total_cost = session.calculate_cost()
    session.status = 'finished'
    session.save()
    messages.success(request, f'سشن پایان یافت. مبلغ کل: ${session.total_cost}')
    return redirect('session_pay', pk=session.pk)


@login_required
def session_pay(request, pk):
    """
    Flexible payment page:
    - Shows total cost, already-paid, remaining
    - Add payment lines: any amount, cash or account (select customer)
    - Can leave and come back later (session stays 'finished')
    """
    session = get_object_or_404(Session, pk=pk, status='finished')
    customers = Customer.objects.all().order_by('name')
    existing_payments = session.payments.all().order_by('created_at')
    players = session.players.select_related('customer')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_payment':
            # Add one payment line
            raw_amount = request.POST.get('amount', '').strip()
            pay_type = request.POST.get('pay_type', 'cash')  # 'cash' or 'account'
            customer_id = request.POST.get('customer_id') or None

            try:
                amount = Decimal(raw_amount)
                if amount <= 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                messages.error(request, 'مبلغ نامعتبر است.')
                return redirect('session_pay', pk=pk)

            if pay_type == 'cash':
                Payment.objects.create(
                    session=session,
                    customer_id=customer_id,
                    amount=amount,
                    payment_type='cash',
                    notes=request.POST.get('notes', ''),
                )
            elif pay_type == 'account':
                if not customer_id:
                    messages.error(request, 'برای پرداخت حسابی، مشتری را انتخاب کنید.')
                    return redirect('session_pay', pk=pk)
                customer = Customer.objects.get(pk=customer_id)
                customer.balance -= amount
                customer.save()
                Payment.objects.create(
                    session=session,
                    customer=customer,
                    amount=amount,
                    payment_type='account_debit',
                    notes=request.POST.get('notes', ''),
                )
            messages.success(request, f'پرداخت ${amount} ثبت شد.')
            return redirect('session_pay', pk=pk)

        elif action == 'delete_payment':
            pay_id = request.POST.get('payment_id')
            try:
                pay = Payment.objects.get(pk=pay_id, session=session)
                # Reverse account_debit
                if pay.payment_type == 'account_debit' and pay.customer:
                    pay.customer.balance += pay.amount
                    pay.customer.save()
                pay.delete()
                messages.success(request, 'پرداخت حذف شد.')
            except Payment.DoesNotExist:
                messages.error(request, 'پرداخت یافت نشد.')
            return redirect('session_pay', pk=pk)

        elif action == 'pay_remaining_cash':
            remaining = session.remaining_amount
            if remaining > 0:
                Payment.objects.create(
                    session=session,
                    amount=remaining,
                    payment_type='cash',
                    notes='باقیمانده نقد',
                )
                messages.success(request, f'باقیمانده ${remaining} نقد دریافت شد.')
            return redirect('session_pay', pk=pk)

        elif action == 'pay_remaining_account':
            remaining = session.remaining_amount
            customer_id = request.POST.get('customer_id')
            if not customer_id:
                messages.error(request, 'مشتری را انتخاب کنید.')
                return redirect('session_pay', pk=pk)
            if remaining > 0:
                customer = Customer.objects.get(pk=customer_id)
                customer.balance -= remaining
                customer.save()
                Payment.objects.create(
                    session=session,
                    customer=customer,
                    amount=remaining,
                    payment_type='account_debit',
                    notes='باقیمانده به حساب',
                )
                messages.success(request, f'باقیمانده ${remaining} به حساب {customer.name} افزوده شد.')
            return redirect('session_pay', pk=pk)

        elif action == 'leave':
            messages.info(request, 'پرداخت بعداً تکمیل می‌شود.')
            return redirect('dashboard')

    return render(request, 'session_pay.html', {
        'session': session,
        'customers': customers,
        'existing_payments': existing_payments,
        'players': players,
    })


@login_required
def session_status(request, pk):
    session = get_object_or_404(Session, pk=pk)
    return JsonResponse({
        'elapsed_seconds': session.elapsed_seconds,
        'current_cost': float(session.calculate_cost()),
        'duration_display': session.duration_display,
    })


@login_required
def sessions_history(request):
    sessions = (Session.objects.exclude(status='active')
                .order_by('-started_at')
                .select_related('device')[:100])
    return render(request, 'sessions_history.html', {'sessions': sessions})


# ── Customers ─────────────────────────────────────────────────────────────────

@login_required
def customers_list(request):
    q = request.GET.get('q', '')
    customers = Customer.objects.all()
    if q:
        customers = customers.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    return render(request, 'customers_list.html', {
        'customers': customers.order_by('balance', 'name'),
        'q': q,
    })


@login_required
def customer_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'نام الزامی است.')
        else:
            c = Customer.objects.create(
                name=name,
                phone=request.POST.get('phone', '').strip()
            )
            messages.success(request, f'مشتری "{c.name}" ساخته شد.')
            return redirect('customers_list')
    return render(request, 'customer_form.html', {'action': 'ایجاد'})


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    sessions = (Session.objects.filter(players__customer=customer)
                .distinct().order_by('-started_at')[:20])
    sales = Sale.objects.filter(customer=customer).order_by('-sold_at')[:20]
    payments = Payment.objects.filter(customer=customer).order_by('-created_at')[:20]
    return render(request, 'customer_detail.html', {
        'customer': customer,
        'sessions': sessions,
        'sales': sales,
        'payments': payments,
    })


@login_required
def customer_settle(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
            if amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            messages.error(request, 'مبلغ نامعتبر.')
            return redirect('customer_detail', pk=pk)

        customer.balance += amount
        customer.save()
        Payment.objects.create(
            customer=customer,
            amount=amount,
            payment_type='account_settlement',
            notes=request.POST.get('notes', ''),
        )
        messages.success(request, f'${amount} برای {customer.name} تسویه شد.')
    return redirect('customer_detail', pk=pk)