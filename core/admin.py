from django.contrib import admin
from .models import Device, Customer, Session, SessionPlayer, Product, ProductCategory, Sale, Payment

@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'device_type', 'price_per_hour', 'is_active']
    list_filter = ['device_type', 'is_active']

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'balance']
    search_fields = ['name', 'phone']

class SessionPlayerInline(admin.TabularInline):
    model = SessionPlayer
    extra = 0

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ['device', 'started_at', 'ended_at', 'status', 'total_cost']
    list_filter = ['status', 'device']
    inlines = [SessionPlayerInline]

@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    pass

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'stock', 'is_active']
    list_filter = ['category', 'is_active']

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['product', 'quantity', 'total_price', 'payment_type', 'sold_at']
    list_filter = ['payment_type']

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['customer', 'amount', 'payment_type', 'created_at']
    list_filter = ['payment_type']
