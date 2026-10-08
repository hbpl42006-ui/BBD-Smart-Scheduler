"""Canonical resolution and availability checks for period-scoped weekly OFF days."""

from academics.models import SectionWeeklyOffPolicy


def get_section_weekly_off_policy(section, academic_session=None, semester=None):
    if section is None:
        return None
    semester = semester or getattr(section, 'semester', None)
    academic_session = academic_session or getattr(semester, 'session', None)
    if semester is None or academic_session is None:
        return None
    cache = getattr(section, '_weekly_off_policy_cache', None)
    if cache is None:
        cache = {}
        section._weekly_off_policy_cache = cache
    key = (str(academic_session.pk), str(semester.pk))
    if key not in cache:
        prefetched = getattr(section, '_prefetched_objects_cache', {}).get('weekly_off_policies')
        if prefetched is not None:
            cache[key] = next((policy for policy in prefetched
                               if policy.is_active
                               and policy.academic_session_id == academic_session.pk
                               and policy.semester_id == semester.pk), None)
        else:
            cache[key] = SectionWeeklyOffPolicy.objects.filter(
                section=section,
                academic_session=academic_session,
                semester=semester,
                is_active=True,
            ).first()
    return cache[key]


def is_section_available(section, weekday, academic_session=None, semester=None):
    policy = get_section_weekly_off_policy(section, academic_session, semester)
    return policy is None or policy.policy_type == SectionWeeklyOffPolicy.PolicyType.NO_WEEKLY_OFF or policy.weekday != weekday


def weekly_off_label(policy):
    if policy is None:
        return 'Source not provided'
    if policy.policy_type == SectionWeeklyOffPolicy.PolicyType.NO_WEEKLY_OFF:
        return 'No weekly OFF day (Mon-Fri allowed)'
    return f'{policy.get_weekday_display()} OFF'
