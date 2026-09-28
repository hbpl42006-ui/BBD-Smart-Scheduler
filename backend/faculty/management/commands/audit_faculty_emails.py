import json
from django.core.management.base import BaseCommand
from faculty.email_service import audit_faculty_emails
class Command(BaseCommand):
    help = 'Report Faculty/User email consistency without modifying data.'
    def handle(self, *args, **options): self.stdout.write(json.dumps(audit_faculty_emails(), indent=2, default=str))
