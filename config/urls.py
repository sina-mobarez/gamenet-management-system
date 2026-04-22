from django.contrib import admin
from django.urls import path
from core import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/',  views.login_view,  name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('',        views.dashboard,   name='dashboard'),

    # Sessions
    path('session/start/',           views.session_start,   name='session_start'),
    path('session/<int:pk>/',        views.session_detail,  name='session_detail'),
    path('session/<int:pk>/end/',    views.session_end,     name='session_end'),
    path('session/<int:pk>/cancel/', views.session_cancel,  name='session_cancel'),
    path('session/<int:pk>/pay/',    views.session_pay,     name='session_pay'),
    path('session/<int:pk>/status/', views.session_status,  name='session_status'),
    path('sessions/history/',        views.sessions_history,name='sessions_history'),

    # Customers
    path('customers/',                 views.customers_list,   name='customers_list'),
    path('customers/create/',          views.customer_create,  name='customer_create'),
    path('customers/<int:pk>/',        views.customer_detail,  name='customer_detail'),
    path('customers/<int:pk>/edit/',   views.customer_edit,    name='customer_edit'),
    path('customers/<int:pk>/delete/', views.customer_delete,  name='customer_delete'),
    path('customers/<int:pk>/settle/', views.customer_settle,  name='customer_settle'),

    # Shop
    path('shop/',                    views.shop,                name='shop'),
    path('shop/sell/',               views.shop_sell,           name='shop_sell'),
    path('products/',                views.products_manage,     name='products_manage'),
    path('products/create/',         views.product_create,      name='product_create'),
    path('products/<int:pk>/edit/',  views.product_edit,        name='product_edit'),
    path('products/<int:pk>/delete/',views.product_delete,      name='product_delete'),
    path('products/<int:pk>/stock/', views.product_update_stock,name='product_update_stock'),

    # Devices
    path('devices/',                  views.devices_manage, name='devices_manage'),
    path('devices/create/',           views.device_create,  name='device_create'),
    path('devices/<int:pk>/edit/',    views.device_edit,    name='device_edit'),
    path('devices/<int:pk>/delete/',  views.device_delete,  name='device_delete'),

    # Reports
    path('reports/', views.reports, name='reports'),
    path('debts/',   views.debts,   name='debts'),
]
