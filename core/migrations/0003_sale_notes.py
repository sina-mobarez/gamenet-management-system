from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_customer_debt_limit_session_duration_minutes_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='notes',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
    ]
