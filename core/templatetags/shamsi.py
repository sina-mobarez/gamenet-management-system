import jdatetime
from django import template
from django.utils import timezone

register = template.Library()

PERSIAN_MONTHS = [
    '', 'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
    'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'
]

PERSIAN_WEEKDAYS = {
    0: 'دوشنبه', 1: 'سه‌شنبه', 2: 'چهارشنبه',
    3: 'پنج‌شنبه', 4: 'جمعه', 5: 'شنبه', 6: 'یکشنبه'
}

def to_persian_digits(s):
    persian = '۰۱۲۳۴۵۶۷۸۹'
    return ''.join(persian[int(c)] if c.isdigit() else c for c in str(s))


@register.filter(name='shamsi')
def shamsi_date(value, fmt='full'):
    """Convert a datetime to Shamsi. fmt: full | short | datetime | time"""
    if not value:
        return '—'
    try:
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        jd = jdatetime.datetime.fromgregorian(datetime=value)
        if fmt == 'short':
            result = f"{jd.year}/{jd.month:02d}/{jd.day:02d}"
        elif fmt == 'time':
            result = f"{jd.hour:02d}:{jd.minute:02d}"
        elif fmt == 'datetime':
            result = f"{jd.year}/{jd.month:02d}/{jd.day:02d}  {jd.hour:02d}:{jd.minute:02d}"
        else:  # full
            month_name = PERSIAN_MONTHS[jd.month]
            result = f"{jd.day} {month_name} {jd.year}  ساعت {jd.hour:02d}:{jd.minute:02d}"
        return to_persian_digits(result)
    except Exception:
        return str(value)


@register.filter(name='shamsi_date_only')
def shamsi_date_only(value):
    return shamsi_date(value, fmt='short')


@register.filter(name='pnum')
def persian_number(value):
    """Convert numbers to Persian digits"""
    if value is None:
        return '—'
    return to_persian_digits(value)


@register.simple_tag
def shamsi_today():
    today = jdatetime.date.today()
    month_name = PERSIAN_MONTHS[today.month]
    return to_persian_digits(f"{today.day} {month_name} {today.year}")
