from faculty.models import FacultyWorkloadPolicy, FacultyWorkloadOverride


DEFAULT_DAILY_PERIODS = 4
DEFAULT_CONSECUTIVE_PERIODS = 2


def workload_limits(faculty_id):
    """Return effective daily and consecutive limits for a faculty member."""
    defaults = FacultyWorkloadPolicy.objects.first()
    daily = defaults.max_daily_periods if defaults else DEFAULT_DAILY_PERIODS
    consecutive = defaults.max_consecutive_periods if defaults else DEFAULT_CONSECUTIVE_PERIODS
    override = FacultyWorkloadOverride.objects.filter(faculty_id=faculty_id, enabled=True).first()
    if override:
        daily = override.max_daily_periods
        consecutive = override.max_consecutive_periods
    return daily, consecutive


def workload_limits_by_faculty(faculty_ids):
    defaults = FacultyWorkloadPolicy.objects.first()
    daily_default = defaults.max_daily_periods if defaults else DEFAULT_DAILY_PERIODS
    consecutive_default = defaults.max_consecutive_periods if defaults else DEFAULT_CONSECUTIVE_PERIODS
    limits = {str(fid): (daily_default, consecutive_default) for fid in faculty_ids}
    for override in FacultyWorkloadOverride.objects.filter(faculty_id__in=faculty_ids, enabled=True):
        limits[str(override.faculty_id)] = (override.max_daily_periods, override.max_consecutive_periods)
    return limits
