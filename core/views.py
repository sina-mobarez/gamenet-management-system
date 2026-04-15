from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from .models import (
    Device, Customer, Session, SessionPlayer,
    Product, ProductCategory, Sale, Payment
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


# ── Shop ──────────────────────────────────────────────────────────────────────

@login_required
def shop(request):
    products = (Product.objects.filter(is_active=True)
                .select_related('category').order_by('category__name', 'name'))
    active_sessions = Session.objects.filter(status='active').select_related('device')
    customers = Customer.objects.all().order_by('name')
    return render(request, 'shop.html', {
        'products': products,
        'active_sessions': active_sessions,
        'customers': customers,
    })


@login_required
def shop_sell(request):
    if request.method != 'POST':
        return redirect('shop')

    product = get_object_or_404(Product, pk=request.POST.get('product_id'))
    qty = int(request.POST.get('quantity', 1))
    payment_type = request.POST.get('payment_type', 'cash')
    session_id = request.POST.get('session_id') or None
    customer_id = request.POST.get('customer_id') or None

    if product.stock < qty:
        messages.error(request, f'موجودی کافی نیست برای {product.name}.')
        return redirect('shop')

    sale = Sale.objects.create(
        product=product,
        quantity=qty,
        unit_price=product.price,
        payment_type=payment_type,
        session_id=session_id,
        customer_id=customer_id,
    )
    product.stock -= qty
    product.save()

    if payment_type == 'account' and customer_id:
        customer = Customer.objects.get(pk=customer_id)
        customer.balance -= sale.total_price
        customer.save()
    elif payment_type == 'cash':
        Payment.objects.create(
            amount=sale.total_price,
            payment_type='cash',
            customer_id=customer_id,
            notes=f'فروش: {product.name} ×{qty}',
        )

    messages.success(request, f'{product.name} ×{qty} به مبلغ ${sale.total_price} فروخته شد.')
    return redirect('shop')


@login_required
def products_manage(request):
    products = (Product.objects.all()
                .select_related('category').order_by('category__name', 'name'))
    categories = ProductCategory.objects.all()
    return render(request, 'products_manage.html', {
        'products': products, 'categories': categories
    })


@login_required
def product_create(request):
    if request.method == 'POST':
        Product.objects.create(
            name=request.POST['name'],
            price=request.POST['price'],
            stock=request.POST.get('stock', 0),
            category_id=request.POST.get('category_id') or None,
            low_stock_threshold=request.POST.get('low_stock_threshold', 5),
        )
        messages.success(request, 'محصول اضافه شد.')
    return redirect('products_manage')


@login_required
def product_update_stock(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.stock = int(request.POST['stock'])
        product.save()
        messages.success(request, f'موجودی {product.name} به‌روز شد.')
    return redirect('products_manage')


# ── Devices ───────────────────────────────────────────────────────────────────

@login_required
def devices_manage(request):
    return render(request, 'devices_manage.html', {
        'devices': Device.objects.all()
    })


@login_required
def device_create(request):
    if request.method == 'POST':
        Device.objects.create(
            name=request.POST['name'],
            device_type=request.POST['device_type'],
            price_per_hour=request.POST['price_per_hour'],
            extra_controller_price=request.POST.get('extra_controller_price', 0),
            included_controllers=request.POST.get('included_controllers', 2),
        )
        messages.success(request, 'دستگاه اضافه شد.')
    return redirect('devices_manage')


@login_required
def device_edit(request, pk):
    device = get_object_or_404(Device, pk=pk)
    if request.method == 'POST':
        device.name = request.POST['name']
        device.price_per_hour = request.POST['price_per_hour']
        device.extra_controller_price = request.POST.get('extra_controller_price', 0)
        device.included_controllers = request.POST.get('included_controllers', 2)
        device.is_active = 'is_active' in request.POST
        device.save()
        messages.success(request, 'دستگاه به‌روز شد.')
        return redirect('devices_manage')
    return render(request, 'device_edit.html', {'device': device})


# ── Reports ───────────────────────────────────────────────────────────────────

@login_required
def reports(request):
    period = request.GET.get('period', 'today')
    now = timezone.now()

    if period == 'week':
        start = now - timedelta(days=7)
    elif period == 'month':
        start = now - timedelta(days=30)
    else:  # today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    session_payments = (
        Payment.objects.filter(created_at__gte=start, payment_type='cash')
        .aggregate(total=Sum('amount'))['total'] or 0
    )
    sales_cash = (
        Sale.objects.filter(sold_at__gte=start, payment_type='cash')
        .aggregate(total=Sum('total_price'))['total'] or 0
    )
    account_charges = (
        Payment.objects.filter(created_at__gte=start, payment_type='account_debit')
        .aggregate(total=Sum('amount'))['total'] or 0
    )
    sales_account = (
        Sale.objects.filter(sold_at__gte=start, payment_type='account')
        .aggregate(total=Sum('total_price'))['total'] or 0
    )
    settlements = (
        Payment.objects.filter(created_at__gte=start, payment_type='account_settlement')
        .aggregate(total=Sum('amount'))['total'] or 0
    )
    period_sessions = Session.objects.filter(
        started_at__gte=start
    ).exclude(status='active').count()

    device_usage = Device.objects.filter(is_active=True).annotate(
        session_count=Count(
            'sessions',
            filter=Q(sessions__started_at__gte=start)
        )
    ).order_by('-session_count')

    top_customers = Customer.objects.annotate(
        session_count=Count(
            'sessionplayer__session', distinct=True,
            filter=Q(sessionplayer__session__started_at__gte=start)
        )
    ).order_by('-session_count')[:10]

    total_debt = abs(
        Customer.objects.filter(balance__lt=0)
        .aggregate(t=Sum('balance'))['t'] or 0
    )

    top_products = (
        Sale.objects.filter(sold_at__gte=start)
        .values('product__name')
        .annotate(total_qty=Sum('quantity'), total_rev=Sum('total_price'))
        .order_by('-total_qty')[:10]
    )

    return render(request, 'reports.html', {
        'period': period,
        'session_cash': float(session_payments),
        'sales_cash': float(sales_cash),
        'total_cash': float(session_payments) + float(sales_cash),
        'account_charges': float(account_charges),
        'sales_account': float(sales_account),
        'total_account': float(account_charges) + float(sales_account),
        'grand_total': float(session_payments) + float(sales_cash) + float(account_charges) + float(sales_account),
        'period_sessions': period_sessions,
        'device_usage': device_usage,
        'top_customers': top_customers,
        'total_debt': float(total_debt),
        'top_products': top_products,
        'settlements': float(settlements),
    })


# ── Debts ─────────────────────────────────────────────────────────────────────

@login_required
def debts(request):
    debtors = Customer.objects.filter(balance__lt=0).order_by('balance')
    return render(request, 'debts.html', {'debtors': debtors})
