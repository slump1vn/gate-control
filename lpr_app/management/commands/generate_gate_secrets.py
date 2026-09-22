from django.core.management.base import BaseCommand

from lpr_app.utils.secrets import generate_encryption_key, generate_token


class Command(BaseCommand):
    help = 'Print new values for GATE_CONFIG_ENCRYPTION_KEY and GATE_AGENT_TOKEN to put in .env'

    def handle(self, *args, **options):
        self.stdout.write(f'GATE_CONFIG_ENCRYPTION_KEY={generate_encryption_key()}')
        self.stdout.write(f'GATE_AGENT_TOKEN={generate_token()}')
        self.stderr.write(
            'Back up GATE_CONFIG_ENCRYPTION_KEY: if it is lost, stored camera passwords '
            'and controller tokens must be re-entered.'
        )
