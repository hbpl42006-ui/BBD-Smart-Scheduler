"""The institution's normal Monday-Friday teaching calendar."""

WORKING_DAYS = (0, 1, 2, 3, 4)
WEEKEND_DAYS = frozenset((5, 6))
WEEKDAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
WEEKEND_ERROR_CODE = 'WEEKEND_SCHEDULING_NOT_ALLOWED'
WEEKEND_ERROR_MESSAGE = 'Classes can only be scheduled Monday through Friday.'


def is_working_day(day):
    try:
        return int(day) in WORKING_DAYS
    except (TypeError, ValueError):
        return False
