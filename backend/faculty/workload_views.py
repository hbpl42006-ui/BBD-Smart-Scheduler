from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from common.models import TimeSlot
from common.permissions import GenerationPermission
from faculty.models import Faculty, FacultyWorkloadPolicy, FacultyWorkloadOverride


def _policy_payload():
    from collections import defaultdict
    from common.models import TimeSlot
    from faculty.models import FacultyAvailability
    from scheduling.models import TimetableVersion
    policy = FacultyWorkloadPolicy.objects.first()
    faculty_rows = list(Faculty.objects.filter(active=True).select_related('department__institution').order_by('name', 'initials'))
    latest_by_institution = {}
    for version in TimetableVersion.objects.filter(status=TimetableVersion.Status.PUBLISHED).select_related('timetable__institution').order_by('-published_at', '-version_no'):
        latest_by_institution.setdefault(version.timetable.institution_id, version)
    weekly = defaultdict(int)
    available_days = {}
    for faculty in faculty_rows:
        version = latest_by_institution.get(faculty.department.institution_id)
        if version:
            for block, in version.entries.filter(faculty_assignments__faculty=faculty).values_list('block_length',):
                weekly[str(faculty.pk)] += block
        slots = list(TimeSlot.objects.filter(template__institution=faculty.department.institution, is_break=False))
        unavailable = {(row.weekday, str(row.time_slot_id)) for row in FacultyAvailability.objects.filter(faculty=faculty, is_available=False)}
        available_days[str(faculty.pk)] = sum(any((day, str(slot.pk)) not in unavailable for slot in slots) for day in range(5))
    effective_daily = policy.max_daily_periods if policy else 4
    effective_consecutive = policy.max_consecutive_periods if policy else 2
    overrides = {str(row.faculty_id): row for row in FacultyWorkloadOverride.objects.filter(enabled=True)}
    return {
        'defaults': {'max_daily_periods': policy.max_daily_periods if policy else 4,
                     'max_consecutive_periods': policy.max_consecutive_periods if policy else 2},
        'overrides': [{'faculty_id': str(row.faculty_id), 'faculty_name': row.faculty.name or row.faculty.initials or row.faculty.employee_code,
                       'enabled': row.enabled, 'max_daily_periods': row.max_daily_periods,
                       'max_consecutive_periods': row.max_consecutive_periods}
                      for row in FacultyWorkloadOverride.objects.select_related('faculty').order_by('faculty__name')],
        'faculty': [{'id': str(row.pk), 'name': row.name or row.initials or row.employee_code or 'Unnamed faculty',
                     'weekly_periods': weekly[str(row.pk)], 'available_days': available_days[str(row.pk)],
                     'suggested_daily_periods': ((weekly[str(row.pk)] + available_days[str(row.pk)] - 1) // available_days[str(row.pk)]) if available_days[str(row.pk)] else None,
                     'max_daily_periods': overrides[str(row.pk)].max_daily_periods if str(row.pk) in overrides else effective_daily,
                     'max_consecutive_periods': overrides[str(row.pk)].max_consecutive_periods if str(row.pk) in overrides else effective_consecutive}
                    for row in faculty_rows],
    }


def _validate(daily, consecutive, faculty=None):
    try:
        daily, consecutive = int(daily), int(consecutive)
    except (TypeError, ValueError):
        return None, Response({'detail': 'Daily and consecutive limits must be integers.'}, status=400)
    if daily < 1 or consecutive < 1 or consecutive > daily:
        return None, Response({'detail': 'Limits must be positive and consecutive periods cannot exceed daily periods.'}, status=400)
    templates = TimeSlot.objects.filter(is_break=False)
    if faculty:
        templates = templates.filter(template__institution=faculty.department.institution)
    counts = {}
    for template_id in templates.values_list('template_id', flat=True).distinct():
        counts[template_id] = templates.filter(template_id=template_id).count()
    capacity = max(counts.values(), default=None)
    if capacity is not None and daily > capacity:
        return None, Response({'detail': f'Daily limit cannot exceed the {capacity} teaching periods in the available canonical template.'}, status=400)
    return (daily, consecutive), None


class FacultyWorkloadPolicyView(APIView):
    permission_classes = [IsAuthenticated, GenerationPermission]

    def get(self, request):
        return Response(_policy_payload())

    def put(self, request):
        limits, error = _validate(request.data.get('max_daily_periods'), request.data.get('max_consecutive_periods'))
        if error:
            return error
        with transaction.atomic():
            policy = FacultyWorkloadPolicy.objects.first()
            policy = policy or FacultyWorkloadPolicy()
            policy.max_daily_periods, policy.max_consecutive_periods = limits
            policy.save()
        return Response(_policy_payload())


class FacultyWorkloadOverrideView(APIView):
    permission_classes = [IsAuthenticated, GenerationPermission]

    def post(self, request):
        faculty = Faculty.objects.filter(pk=request.data.get('faculty_id'), active=True).select_related('department__institution').first()
        if not faculty:
            return Response({'detail': 'Select an active Faculty record.'}, status=400)
        limits, error = _validate(request.data.get('max_daily_periods'), request.data.get('max_consecutive_periods'), faculty)
        if error:
            return error
        override, _ = FacultyWorkloadOverride.objects.update_or_create(faculty=faculty, defaults={
            'enabled': bool(request.data.get('enabled', True)), 'max_daily_periods': limits[0],
            'max_consecutive_periods': limits[1],
        })
        return Response(_policy_payload())
