"""Resolve section delivery rules in their academic-period scope."""


def get_section_delivery_policy_record(section, semester=None):
    """Return the active period policy, if one applies to this section."""
    if section is None:
        return None
    semester = semester or getattr(section, 'semester', None)
    if semester is None:
        return None
    cache = getattr(section, '_delivery_policy_cache', None)
    if cache is None:
        cache = {}
        section._delivery_policy_cache = cache
    key = str(semester.pk)
    if key not in cache:
        prefetched = getattr(section, '_prefetched_objects_cache', {}).get('period_delivery_policies')
        if prefetched is not None:
            cache[key] = next((policy for policy in prefetched
                               if policy.active
                               and policy.semester_id == semester.pk
                               and policy.academic_session_id == semester.session_id), None)
        else:
            cache[key] = section.period_delivery_policies.filter(
                active=True,
                semester_id=semester.pk,
                academic_session_id=semester.session_id,
            ).first()
    return cache[key]


def get_section_delivery_policy(section, semester=None):
    """Return ``(mode, offline_weekday)`` for a section and semester.

    Period-scoped policies take precedence. The legacy fields remain a fallback
    for existing data and backwards-compatible callers that have not migrated.
    """
    if section is None:
        return 'STANDARD', None
    policy = get_section_delivery_policy_record(section, semester)
    return (policy.mode, policy.offline_weekday) if policy else (
        section.delivery_policy,
        section.offline_weekday,
    )


def expected_delivery_mode(section, weekday, semester=None):
    mode, offline_weekday = get_section_delivery_policy(section, semester)
    return 'ONLINE' if mode == 'HYBRID' and weekday != offline_weekday else 'OFFLINE'
