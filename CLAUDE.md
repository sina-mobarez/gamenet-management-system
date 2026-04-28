# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt
python manage.py migrate          # first run only
python manage.py runserver        # start dev server at http://127.0.0.1:8000
python manage.py changepassword admin
python manage.py collectstatic    # after adding static files
```

Default login: `admin` / `admin123`

Backup: copy `db.sqlite3` — all data is there.

Docker:
```bash
docker build -t gamenet .
docker run -p 8000:8000 gamenet
```

## Architecture

Single Django app (`core`) with all business logic. `config/` holds project settings, URL routing, and WSGI/ASGI entry points.

```
config/       Django project (settings, urls, wsgi)
core/         The only app — models, views, admin, templatetags
templates/    All HTML templates (project root, not inside app)
static/       Source static files (Bootstrap RTL, fonts, JS)
staticfiles/  Collected output (generated, not edited directly)
```

`TIME_ZONE = 'Asia/Tehran'`, `ALLOWED_HOSTS = ['*']`, SQLite database. No environment-specific config files — settings are hardcoded for local/self-hosted use.

## Domain models

All models live in `core/models.py`:

- **Device** — gaming equipment (billiard, snooker, ps4, ps5, airHocky, system, tennis). Tracks `price_per_hour`, `extra_controller_price`, `included_controllers`. `is_occupied` checks for an active session; `unpaid_session` finds the first finished-but-unpaid session.
- **Session** — a device usage period (`active` → `finished` or `cancelled`). Two types: `free` (open-ended, cost calculated at end) and `timed` (fixed `duration_minutes`). `calculate_cost()` rounds to the nearest 1,000 تومان. `total_cost` is set only when the session ends.
- **SessionPlayer** — joins a Session to a Customer (registered) or a plain name string (guest).
- **Customer** — `balance` field: negative = owes money (debt), positive = credit. `debt_limit` caps allowed debt (0 = unlimited). `can_add_debt()` enforces the cap.
- **Payment** — three types: `cash`, `account_debit` (reduces customer balance), `account_settlement` (increases customer balance to settle debt). Can be linked to a session and/or a customer.
- **Product / ProductCategory / Sale** — café shop inventory and sales. Sales can be optionally linked to an active session and/or customer.

## Session lifecycle

1. Dashboard → click free device → modal → `POST /session/start/` → Session created with status `active`
2. Click active device → `/session/<pk>/` detail page (live timer via `/session/<pk>/status/` polling)
3. End session → `POST /session/<pk>/end/` → sets `ended_at`, calculates `total_cost`, marks `finished`, redirects to pay page
4. Pay page (`/session/<pk>/pay/`) handles multiple partial payments; action=`leave` exits without full payment (session shows yellow on dashboard)
5. Deleting a payment of type `account_debit` reverses the customer balance change

## Access control

- Reports page (`/reports/`) and CSV export (`/reports/csv/`) require `user.is_superuser`.
- Dashboard income/debt summary stat cards are hidden for non-superusers (template `{% if user.is_superuser %}`).
- Reports nav link in sidebar also only shows for superusers.

## Template tags (`core/templatetags/shamsi.py`)

Load with `{% load shamsi %}` in templates.

| Tag/Filter | Usage | Output |
|---|---|---|
| `\|shamsi` | `{{ obj.created_at\|shamsi }}` | Full Persian date + time |
| `\|shamsi:"short"` | short format | `1403/01/15` |
| `\|shamsi:"datetime"` | compact | `1403/01/15  14:30` |
| `\|shamsi:"time"` | time only | `14:30` |
| `\|pnum` | `{{ value\|pnum }}` | Converts to Persian digits |
| `{% shamsi_today %}` | tag | Today's Shamsi date |

## UI conventions

- Bootstrap RTL (`bootstrap.rtl.min.css`) — layout is right-to-left
- Vazirmatn (Persian), Space Grotesk (numbers/Latin display — replaces the old Oxanium font)
- All user-facing text and messages are in Persian (Farsi)
- Django messages framework used for flash notifications
- Dashboard tiles auto-refresh session timers via `setInterval` calling the `/status/` JSON endpoint
- Dashboard groups devices by `device_type` (template loops over `device_groups` from the view)
- Shop uses a JS cart (multi-select, quantities, deferred/cash/account payment) posted to `shop_sell`
- Deferred café sales: `Sale.payment_type='account'`, `customer=None`, linked to session — shown as café tab in session_pay
