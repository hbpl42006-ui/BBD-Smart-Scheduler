from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from accounts.models import User

def normalize_email(value):
    value = (value or '').strip().lower()
    if value: validate_email(value)
    return value or None

@transaction.atomic
def sync_faculty_email(faculty, new_email):
    email = normalize_email(new_email)
    if email:
        conflict = User.objects.filter(email__iexact=email).exclude(pk=faculty.user_id if faculty.user_id else None).first()
        if conflict: raise ValidationError('This email is already used by another user account.')
    faculty.email = email; faculty.save(update_fields=['email', 'updated_at'])
    if faculty.user_id:
        faculty.user.email = email; faculty.user.save(update_fields=['email', 'updated_at'])
    return faculty

def audit_faculty_emails():
    from .models import Faculty
    faculties = list(Faculty.objects.select_related('user').all())
    grouped = {}
    for faculty in faculties:
        if faculty.email: grouped.setdefault(faculty.email.lower(), []).append(faculty.pk)
    return {'mismatched_linked': [{'faculty_id': str(f.pk), 'faculty_email': f.email, 'user_email': f.user.email} for f in faculties if f.user and f.email and f.email.lower() != f.user.email.lower()], 'linked_faculty_email_blank': [{'faculty_id': str(f.pk), 'user_id': str(f.user_id), 'user_email': f.user.email} for f in faculties if f.user and not f.email], 'duplicate_faculty_emails': [{'email': email, 'faculty_ids': [str(pk) for pk in ids]} for email, ids in grouped.items() if len(ids) > 1], 'faculty_email_user_conflicts': [{'faculty_id': str(f.pk), 'email': f.email} for f in faculties if f.email and User.objects.filter(email__iexact=f.email).exclude(pk=f.user_id if f.user_id else None).exists()], 'faculty_users_without_profile': [{'user_id': str(u.pk), 'email': u.email} for u in User.objects.filter(role='FACULTY').exclude(faculty_profile__isnull=False)]}
