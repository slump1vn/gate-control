from django.db import migrations

GATE_ADMIN_PERMS = [
    ('vehicle', ['add', 'change', 'delete', 'view']),
    ('camera', ['add', 'change', 'delete', 'view']),
    ('gatedevice', ['add', 'change', 'delete', 'view']),
    ('accessevent', ['view']),
    ('gateconfigchange', ['view']),
]

GATE_OPERATOR_PERMS = [
    ('vehicle', ['add', 'change', 'view']),
    ('accessevent', ['view']),
]


def _ensure_permissions(apps):
    # Permissions are normally created after migrate; create them now so the
    # groups below can reference them on a fresh database.
    from django.contrib.auth.management import create_permissions
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None


def create_groups(apps, schema_editor):
    _ensure_permissions(apps)
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    for name, spec in (('gate_admin', GATE_ADMIN_PERMS), ('gate_operator', GATE_OPERATOR_PERMS)):
        group, _ = Group.objects.get_or_create(name=name)
        codenames = [f'{action}_{model}' for model, actions in spec for action in actions]
        perms = Permission.objects.filter(content_type__app_label='lpr_app', codename__in=codenames)
        group.permissions.set(perms)


def delete_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['gate_admin', 'gate_operator']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('lpr_app', '0003_gate_automation'),
    ]

    operations = [
        migrations.RunPython(create_groups, delete_groups),
    ]
