import json
import re

from django.core.management.base import BaseCommand

from accounts.models import Role, User
from faculty.models import Faculty


PLACEHOLDER = re.compile(r'^faculty\d+@example\.com$', re.IGNORECASE)


class Command(BaseCommand):
    help = 'Read-only audit of generated-looking Faculty User accounts.'

    def handle(self, *args, **options):
        rows = []
        for user in User.objects.select_related('faculty_profile').filter(email__iregex=r'^faculty[0-9]+@example\.com$'):
            faculty = getattr(user, 'faculty_profile', None)
            dependency_counts = {
                'notifications': user.notifications.count(),
                'notification_preferences': int(hasattr(user, 'notification_preferences')),
                'audit_events': user.audit_events.count(),
                'coordinated_sections': user.coordinated_sections.count(),
                'created_timetables': user.created_timetables.count(),
                'created_timetable_versions': user.created_timetable_versions.count(),
                'generation_runs': user.generation_runs.count(),
                'created_faculty_arrangements': user.created_faculty_arrangements.count(),
                'uploaded_arrangement_evidence': user.uploaded_arrangement_evidence.count(),
            }
            if faculty:
                dependency_counts.update({
                    'course_offering_assignments': faculty.course_offerings.count(),
                    'schedule_assignments': faculty.schedule_assignments.count(),
                    'faculty_availability': faculty.availabilities.count(),
                    'absence_arrangements': faculty.absence_arrangements.count(),
                    'substitute_arrangements': faculty.substitute_arrangements.count(),
                })
            has_dependencies = any(dependency_counts.values())
            protected_role = user.role != Role.FACULTY or user.is_superuser or user.is_staff
            rows.append({
                'user_id': str(user.pk),
                'email': user.email,
                'name': f'{user.first_name} {user.last_name}'.strip(),
                'role': user.role,
                'is_active': user.is_active,
                'linked_faculty': ({'id': str(faculty.pk), 'name': faculty.name, 'employee_code': faculty.employee_code} if faculty else None),
                'classification': 'LINKED PLACEHOLDER USER' if faculty else 'ORPHAN PLACEHOLDER USER',
                'safe_to_remove': bool(not faculty and not has_dependencies and not protected_role),
                'requires_manual_review': bool(faculty or has_dependencies or protected_role),
                'dependency_counts': dependency_counts,
            })
        summary = {
            'placeholder_users_found': len(rows),
            'orphan': sum(row['classification'] == 'ORPHAN PLACEHOLDER USER' for row in rows),
            'linked_to_faculty': sum(row['classification'] == 'LINKED PLACEHOLDER USER' for row in rows),
            'safe_to_remove': sum(row['safe_to_remove'] for row in rows),
            'requires_manual_review': sum(row['requires_manual_review'] for row in rows),
            'users': rows,
            'destructive_action_performed': False,
        }
        self.stdout.write(json.dumps(summary, indent=2))
