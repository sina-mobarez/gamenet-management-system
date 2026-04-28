import csv
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q
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
        user = authenticate(request, username=request.POST['username'],
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
    devices_qs = Device.objects.filter(is_active=True).order_by('device_type', 'name')
    # Group devices by type for categorized display
    device_groups = {}
    for d in devices_qs:
        key = d.get_device_type_display()
        device_groups.setdefault(key, []).append(d)

    low_stock = Product.objects.filter(is_active=True).extra(
        where=["stock <= low_stock_threshold"]
    )

    today      = timezone.localdate()
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
        Customer.objects.filter(balance__lt=0).aggregate(t=Sum('balance'))['t'] or 0
    )

    unpaid_sessions = [
        s for s in Session.objects.filter(status='finished')
                          .select_related('device').order_by('-ended_at')
        if not s.is_fully_paid
    ]

    return render(request, 'dashboard.html', {
        'device_groups':        device_groups.items(),
        'low_stock':            low_stock,
        'today_cash':           today_cash + today_sales_cash,
        'total_debt':           total_debt,
        'customers_for_modal':  Customer.objects.all().order_by('name'),
        'unpaid_sessions':      unpaid_sessions,
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

    session_type     = request.POST.get('session_type', 'free')
    duration_minutes = None
    if session_type == 'timed':
        try:
            duration_minutes = int(request.POST.get('duration_minutes', 60))
            if duration_minutes <= 0:
                duration_minutes = 60
        except (ValueError, TypeError):
            duration_minutes = 60

    session = Session.objects.create(
        device           = device,
        extra_controllers= int(request.POST.get('extra_controllers', 0)),
        session_type     = session_type,
        duration_minutes = duration_minutes,
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
def session_cancel(request, pk):
    """Delete an active session with no payments — for wrong starts."""
    session = get_object_or_404(Session, pk=pk, status='active')
    if request.method == 'POST':
        device_name = session.device.name
        session.delete()
        messages.success(request, f'سشن {device_name} لغو و حذف شد.')
    return redirect('dashboard')


@login_required
def session_detail(request, pk):
    session      = get_object_or_404(Session, pk=pk)
    current_cost = session.calculate_cost()
    return render(request, 'session_detail.html', {
        'session':      session,
        'current_cost': current_cost,
        'customers':    Customer.objects.all().order_by('name'),
    })


@login_required
def session_end(request, pk):
    session           = get_object_or_404(Session, pk=pk, status='active')
    session.ended_at  = timezone.now()
    session.total_cost = session.calculate_cost()
    session.status    = 'finished'
    session.save()
    messages.success(request, f'سشن پایان یافت. مبلغ: {session.total_cost:,} تومان')
    return redirect('session_pay', pk=session.pk)


@login_required
def session_pay(request, pk):
    session            = get_object_or_404(Session, pk=pk, status='finished')
    customers          = Customer.objects.all().order_by('name')
    existing_payments  = session.payments.all().order_by('created_at')
    players            = session.players.select_related('customer')
    cafe_tab_items     = session.sales.filter(payment_type='account', customer__isnull=True)
    cafe_tab_total     = cafe_tab_items.aggregate(t=Sum('total_price'))['t'] or 0

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_payment':
            raw       = request.POST.get('amount', '').strip()
            pay_type  = request.POST.get('pay_type', 'cash')
            cust_id   = request.POST.get('customer_id') or None

            try:
                amount = int(raw)
                if amount <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, 'مبلغ نامعتبر است.')
                return redirect('session_pay', pk=pk)

            if pay_type == 'cash':
                Payment.objects.create(session=session, customer_id=cust_id,
                                       amount=amount, payment_type='cash',
                                       notes=request.POST.get('notes', ''))

            elif pay_type == 'account':
                if not cust_id:
                    messages.error(request, 'مشتری را انتخاب کنید.')
                    return redirect('session_pay', pk=pk)
                customer = Customer.objects.get(pk=cust_id)
                ok, reason = customer.can_add_debt(amount)
                if not ok:
                    messages.error(request, f'حساب {customer.name} مسدود است: {reason}')
                    return redirect('session_pay', pk=pk)
                customer.balance -= amount
                customer.save()
                Payment.objects.create(session=session, customer=customer,
                                       amount=amount, payment_type='account_debit',
                                       notes=request.POST.get('notes', ''))

            messages.success(request, f'{amount:,} تومان ثبت شد.')
            return redirect('session_pay', pk=pk)

        elif action == 'delete_payment':
            try:
                pay = Payment.objects.get(pk=request.POST.get('payment_id'), session=session)
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
                Payment.objects.create(session=session, amount=remaining,
                                       payment_type='cash', notes='باقیمانده نقد')
                messages.success(request, f'{remaining:,} تومان نقد دریافت شد.')
            return redirect('session_pay', pk=pk)

        elif action == 'pay_remaining_account':
            remaining = session.remaining_amount
            cust_id   = request.POST.get('customer_id')
            if not cust_id:
                messages.error(request, 'مشتری را انتخاب کنید.')
                return redirect('session_pay', pk=pk)
            if remaining > 0:
                customer = Customer.objects.get(pk=cust_id)
                ok, reason = customer.can_add_debt(remaining)
                if not ok:
                    messages.error(request, f'حساب مسدود است: {reason}')
                    return redirect('session_pay', pk=pk)
                customer.balance -= remaining
                customer.save()
                Payment.objects.create(session=session, customer=customer,
                                       amount=remaining, payment_type='account_debit',
                                       notes='باقیمانده به حساب')
                messages.success(request, f'{remaining:,} تومان به حساب {customer.name} افزوده شد.')
            return redirect('session_pay', pk=pk)

        elif action == 'leave':
            messages.info(request, 'پرداخت بعداً تکمیل می‌شود.')
            return redirect('dashboard')

    total_with_tab    = (session.total_cost or 0) + cafe_tab_total
    remaining_with_tab = max(0, total_with_tab - session.paid_amount)
    fully_paid_with_tab = remaining_with_tab <= 0

    return render(request, 'session_pay.html', {
        'session':             session,
        'customers':           customers,
        'existing_payments':   existing_payments,
        'players':             players,
        'cafe_tab_items':      cafe_tab_items,
        'cafe_tab_total':      cafe_tab_total,
        'total_with_tab':      total_with_tab,
        'remaining_with_tab':  remaining_with_tab,
        'fully_paid_with_tab': fully_paid_with_tab,
    })


@login_required
def session_update(request, pk):
    """Add/remove players or update notes on an active session without ending it."""
    session = get_object_or_404(Session, pk=pk, status='active')
    next_url = request.POST.get('next', 'dashboard')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_notes':
            session.notes = request.POST.get('notes', '')
            session.save()
            messages.success(request, 'یادداشت ذخیره شد.')
        elif action == 'add_player':
            cid  = request.POST.get('customer_id', '').strip()
            name = request.POST.get('player_name', '').strip()
            if cid:
                SessionPlayer.objects.create(session=session, customer_id=cid)
                messages.success(request, 'بازیکن اضافه شد.')
            elif name:
                SessionPlayer.objects.create(session=session, player_name=name)
                messages.success(request, 'بازیکن اضافه شد.')
            else:
                messages.error(request, 'نام یا مشتری را وارد کنید.')
        elif action == 'remove_player':
            SessionPlayer.objects.filter(pk=request.POST.get('player_id'), session=session).delete()
            messages.success(request, 'بازیکن حذف شد.')
    if next_url == 'session_detail':
        return redirect('session_detail', pk=pk)
    return redirect('dashboard')


@login_required
def session_status(request, pk):
    session = get_object_or_404(Session, pk=pk)
    data = {
        'elapsed_seconds':   session.elapsed_seconds,
        'current_cost':      session.calculate_cost(),
        'duration_display':  session.duration_display,
        'remaining_seconds': session.remaining_seconds,
        'status':            session.status,
    }
    return JsonResponse(data)


@login_required
def sessions_history(request):
    sessions = (Session.objects.exclude(status='active')
                .order_by('-started_at').select_related('device')[:100])
    return render(request, 'sessions_history.html', {'sessions': sessions})


# ── Customers ─────────────────────────────────────────────────────────────────

@login_required
def customers_list(request):
    q = request.GET.get('q', '')
    qs = Customer.objects.all()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    return render(request, 'customers_list.html', {
        'customers': qs.order_by('balance', 'name'), 'q': q,
    })


@login_required
def customer_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'نام الزامی است.')
        else:
            initial_debt = int(request.POST.get('initial_debt', 0) or 0)
            c = Customer.objects.create(
                name       = name,
                phone      = request.POST.get('phone', '').strip(),
                debt_limit = int(request.POST.get('debt_limit', 0) or 0),
                balance    = -initial_debt if initial_debt > 0 else 0,
            )
            if initial_debt > 0:
                Payment.objects.create(
                    customer=c, amount=initial_debt,
                    payment_type='account_debit',
                    notes='بدهی اولیه هنگام ثبت‌نام',
                )
            messages.success(request, f'مشتری "{c.name}" ساخته شد.')
            return redirect('customers_list')
    return render(request, 'customer_form.html', {'action': 'ایجاد', 'customer': None})


@login_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        customer.name       = request.POST.get('name', customer.name).strip()
        customer.phone      = request.POST.get('phone', '').strip()
        customer.debt_limit = int(request.POST.get('debt_limit', 0) or 0)
        customer.notes      = request.POST.get('notes', '')
        customer.save()
        messages.success(request, 'مشتری به‌روز شد.')
        return redirect('customer_detail', pk=pk)
    return render(request, 'customer_form.html', {'action': 'ویرایش', 'customer': customer})


@login_required
def customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        if customer.balance != 0:
            messages.error(request, 'امکان حذف مشتری با موجودی غیرصفر وجود ندارد.')
            return redirect('customer_detail', pk=pk)
        name = customer.name
        customer.delete()
        messages.success(request, f'مشتری "{name}" حذف شد.')
        return redirect('customers_list')
    return render(request, 'confirm_delete.html', {
        'object_name': customer.name,
        'cancel_url':  f'/customers/{pk}/',
    })


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    sessions = (Session.objects.filter(players__customer=customer)
                .distinct().order_by('-started_at')[:20])
    sales    = Sale.objects.filter(customer=customer).order_by('-sold_at')[:20]
    payments = Payment.objects.filter(customer=customer).order_by('-created_at')[:20]
    return render(request, 'customer_detail.html', {
        'customer': customer,
        'sessions': sessions,
        'sales':    sales,
        'payments': payments,
    })


@login_required
def customer_add_debt(request, pk):
    """Manually add a debt (reduce balance) to a customer."""
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        try:
            amount = int(request.POST.get('amount', 0))
            if amount <= 0:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'مبلغ نامعتبر است.')
            return redirect('customer_detail', pk=pk)
        ok, reason = customer.can_add_debt(amount)
        if not ok:
            messages.error(request, reason)
            return redirect('customer_detail', pk=pk)
        customer.balance -= amount
        customer.save()
        Payment.objects.create(
            customer=customer, amount=amount,
            payment_type='account_debit',
            notes=request.POST.get('notes', '') or 'بدهی دستی',
        )
        messages.success(request, f'{amount:,} تومان بدهی برای {customer.name} ثبت شد.')
    return redirect('customer_detail', pk=pk)


@login_required
def customer_settle(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        try:
            amount = int(request.POST.get('amount', 0))
            if amount <= 0:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'مبلغ نامعتبر.')
            return redirect('customer_detail', pk=pk)
        customer.balance += amount
        customer.save()
        Payment.objects.create(customer=customer, amount=amount,
                                payment_type='account_settlement',
                                notes=request.POST.get('notes', ''))
        messages.success(request, f'{amount:,} تومان برای {customer.name} تسویه شد.')
    return redirect('customer_detail', pk=pk)


# ── Shop ──────────────────────────────────────────────────────────────────────

@login_required
def shop(request):
    products       = (Product.objects.filter(is_active=True)
                      .select_related('category').order_by('category__name', 'name'))
    active_sessions = Session.objects.filter(status='active').select_related('device')
    customers      = Customer.objects.all().order_by('name')
    return render(request, 'shop.html', {
        'products':       products,
        'active_sessions': active_sessions,
        'customers':      customers,
    })


@login_required
def shop_sell(request):
    """Multi-product cart sell. payment_type: cash | account | deferred."""
    if request.method != 'POST':
        return redirect('shop')

    pay_type    = request.POST.get('payment_type', 'cash')
    session_id  = request.POST.get('session_id') or None
    customer_id = request.POST.get('customer_id') or None
    product_ids = request.POST.getlist('product_ids[]')
    quantities  = request.POST.getlist('quantities[]')

    if not product_ids:
        messages.error(request, 'هیچ محصولی انتخاب نشده است.')
        return redirect('shop')

    sold_names, errors = [], []
    for pid, qty_str in zip(product_ids, quantities):
        try:
            product = Product.objects.get(pk=pid, is_active=True)
            qty = max(1, int(qty_str))
        except (Product.DoesNotExist, ValueError):
            continue

        if product.stock < qty:
            errors.append(f'موجودی {product.name} کافی نیست.')
            continue

        effective_type = 'account' if pay_type == 'deferred' else pay_type
        cid = None if pay_type == 'deferred' else customer_id

        sale = Sale.objects.create(
            product=product, quantity=qty, unit_price=product.price,
            payment_type=effective_type,
            session_id=session_id, customer_id=cid,
        )
        product.stock -= qty
        product.save()

        if pay_type == 'account' and customer_id:
            customer = Customer.objects.get(pk=customer_id)
            ok, reason = customer.can_add_debt(sale.total_price)
            if not ok:
                sale.delete(); product.stock += qty; product.save()
                errors.append(f'حساب {customer.name} مسدود: {reason}')
                continue
            customer.balance -= sale.total_price
            customer.save()
        elif pay_type == 'cash':
            Payment.objects.create(
                amount=sale.total_price, payment_type='cash',
                customer_id=customer_id, session_id=session_id,
                notes=f'فروش: {product.name} ×{qty}',
            )

        sold_names.append(f'{product.name} ×{qty}')

    for e in errors:
        messages.error(request, e)
    if sold_names:
        label = 'ثبت تب کافه' if pay_type == 'deferred' else 'فروش ثبت شد'
        messages.success(request, f'{label}: {" | ".join(sold_names)}')
    return redirect('shop')


# ── Products ──────────────────────────────────────────────────────────────────

@login_required
def products_manage(request):
    products   = Product.objects.all().select_related('category').order_by('category__name', 'name')
    categories = ProductCategory.objects.all()
    return render(request, 'products_manage.html', {
        'products': products, 'categories': categories
    })


@login_required
def product_create(request):
    if request.method == 'POST':
        Product.objects.create(
            name=request.POST['name'],
            price=int(request.POST.get('price', 0) or 0),
            stock=int(request.POST.get('stock', 0) or 0),
            category_id=request.POST.get('category_id') or None,
            low_stock_threshold=int(request.POST.get('low_stock_threshold', 5) or 5),
        )
        messages.success(request, 'محصول اضافه شد.')
    return redirect('products_manage')


@login_required
def product_edit(request, pk):
    product    = get_object_or_404(Product, pk=pk)
    categories = ProductCategory.objects.all()
    if request.method == 'POST':
        product.name                = request.POST.get('name', product.name)
        product.price               = int(request.POST.get('price', 0) or 0)
        product.stock               = int(request.POST.get('stock', 0) or 0)
        product.category_id         = request.POST.get('category_id') or None
        product.low_stock_threshold = int(request.POST.get('low_stock_threshold', 5) or 5)
        product.is_active           = 'is_active' in request.POST
        product.save()
        messages.success(request, 'محصول به‌روز شد.')
        return redirect('products_manage')
    return render(request, 'product_edit.html', {'product': product, 'categories': categories})


@login_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        name = product.name
        product.delete()
        messages.success(request, f'محصول "{name}" حذف شد.')
        return redirect('products_manage')
    return render(request, 'confirm_delete.html', {
        'object_name': product.name,
        'cancel_url':  '/products/',
    })


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
    return render(request, 'devices_manage.html', {'devices': Device.objects.all()})


@login_required
def device_create(request):
    if request.method == 'POST':
        Device.objects.create(
            name                   = request.POST['name'],
            device_type            = request.POST['device_type'],
            price_per_hour         = int(request.POST.get('price_per_hour', 0) or 0),
            extra_controller_price = int(request.POST.get('extra_controller_price', 0) or 0),
            included_controllers   = int(request.POST.get('included_controllers', 2) or 2),
        )
        messages.success(request, 'دستگاه اضافه شد.')
    return redirect('devices_manage')


@login_required
def device_edit(request, pk):
    device = get_object_or_404(Device, pk=pk)
    if request.method == 'POST':
        device.name                   = request.POST['name']
        device.price_per_hour         = int(request.POST.get('price_per_hour', 0) or 0)
        device.extra_controller_price = int(request.POST.get('extra_controller_price', 0) or 0)
        device.included_controllers   = int(request.POST.get('included_controllers', 2) or 2)
        device.is_active              = 'is_active' in request.POST
        device.save()
        messages.success(request, 'دستگاه به‌روز شد.')
        return redirect('devices_manage')
    return render(request, 'device_edit.html', {'device': device})


@login_required
def device_delete(request, pk):
    device = get_object_or_404(Device, pk=pk)
    if request.method == 'POST':
        if device.sessions.filter(status='active').exists():
            messages.error(request, 'دستگاه در حال استفاده است.')
            return redirect('devices_manage')
        name = device.name
        device.delete()
        messages.success(request, f'دستگاه "{name}" حذف شد.')
        return redirect('devices_manage')
    return render(request, 'confirm_delete.html', {
        'object_name': device.name,
        'cancel_url':  '/devices/',
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@login_required
def reports(request):
    if not request.user.is_superuser:
        messages.error(request, 'فقط سوپریوزر به گزارش‌ها دسترسی دارد.')
        return redirect('dashboard')
    period = request.GET.get('period', 'today')
    now    = timezone.now()
    if period == 'week':
        start = now - timedelta(days=7)
    elif period == 'month':
        start = now - timedelta(days=30)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    session_cash  = Payment.objects.filter(created_at__gte=start, payment_type='cash').aggregate(t=Sum('amount'))['t'] or 0
    sales_cash    = Sale.objects.filter(sold_at__gte=start, payment_type='cash').aggregate(t=Sum('total_price'))['t'] or 0
    acct_charges  = Payment.objects.filter(created_at__gte=start, payment_type='account_debit').aggregate(t=Sum('amount'))['t'] or 0
    sales_account = Sale.objects.filter(sold_at__gte=start, payment_type='account').aggregate(t=Sum('total_price'))['t'] or 0
    settlements   = Payment.objects.filter(created_at__gte=start, payment_type='account_settlement').aggregate(t=Sum('amount'))['t'] or 0
    total_debt    = abs(Customer.objects.filter(balance__lt=0).aggregate(t=Sum('balance'))['t'] or 0)

    device_usage = Device.objects.filter(is_active=True).annotate(
        session_count=Count('sessions', filter=Q(sessions__started_at__gte=start))
    ).order_by('-session_count')

    top_customers = Customer.objects.annotate(
        session_count=Count('sessionplayer__session', distinct=True,
                            filter=Q(sessionplayer__session__started_at__gte=start))
    ).order_by('-session_count')[:10]

    top_products = (Sale.objects.filter(sold_at__gte=start)
                    .values('product__name')
                    .annotate(total_qty=Sum('quantity'), total_rev=Sum('total_price'))
                    .order_by('-total_qty')[:10])

    return render(request, 'reports.html', {
        'period':         period,
        'session_cash':   session_cash,
        'sales_cash':     sales_cash,
        'total_cash':     session_cash + sales_cash,
        'acct_charges':   acct_charges,
        'sales_account':  sales_account,
        'total_account':  acct_charges + sales_account,
        'grand_total':    session_cash + sales_cash + acct_charges + sales_account,
        'period_sessions': Session.objects.filter(started_at__gte=start).exclude(status='active').count(),
        'device_usage':   device_usage,
        'top_customers':  top_customers,
        'total_debt':     total_debt,
        'top_products':   top_products,
        'settlements':    settlements,
    })


@login_required
def reports_csv(request):
    if not request.user.is_superuser:
        return redirect('dashboard')
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="customers_debts.csv"'
    writer = csv.writer(response)
    writer.writerow(['نام', 'تلفن', 'موجودی', 'بدهی'])
    for c in Customer.objects.all().order_by('name'):
        writer.writerow([
            c.name, c.phone,
            c.balance,
            abs(c.balance) if c.balance < 0 else 0,
        ])
    return response


# ── Debts ─────────────────────────────────────────────────────────────────────

@login_required
def debts(request):
    return render(request, 'debts.html', {
        'debtors': Customer.objects.filter(balance__lt=0).order_by('balance')
    })
