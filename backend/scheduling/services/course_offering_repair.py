"""Plan a conservative master-data repair from a timetable import workbook."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db import transaction

from academics.models import Course, CourseOffering, Section
from faculty.models import CourseOfferingFaculty, Faculty
from common.course_offering_import import canonical_room_code
from rooms.services.eligibility import room_section_error
from scheduling.models import TimetableVersion
from scheduling.services.timetable_import import _faculty_display_name, _rows, parse
from scheduling.services.faculty_names import faculty_name_matches


VALID_ROLES = {'PRIMARY', 'CO_FACULTY', 'ASSISTANT'}
CONFIG_FIELDS = (
    'weekly_periods', 'default_class_type', 'required_block_size',
    'allow_remainder_period', 'room_type_requirement',
)


def _text(value):
    return str(value or '').strip()


def _source_periods(rows):
    """Count distinct placement blocks, not faculty rows or credit hours."""
    placements = {}
    for row in rows:
        day, slot = _text(row.get('day')).casefold(), _text(row.get('time_slot')).casefold()
        if not day or not slot:
            return None
        raw_length = _text(row.get('block_length')) or '1'
        try:
            length = Decimal(raw_length)
            if not length.is_finite() or length < 1 or length != int(length):
                return None
        except (InvalidOperation, ValueError):
            return None
        key = (day, slot)
        if key in placements and placements[key] != int(length):
            return None
        placements[key] = int(length)
    return sum(placements.values()) if placements else None


def _configuration(semester, section, course, source_rows):
    siblings = list(CourseOffering.objects.filter(
        semester=semester, section__program_id=section.program_id,
        section__year=section.year, course=course, active=True,
    ).exclude(section=section).select_related('preferred_room').order_by('section__name', 'pk'))
    if siblings:
        configurations = {tuple(getattr(item, field) for field in CONFIG_FIELDS) for item in siblings}
        if len(configurations) != 1:
            source_rooms = {canonical_room_code(_text(row.get('room'))) for row in source_rows}
            if len(source_rooms) == 1:
                matching = [item for item in siblings if item.preferred_room and canonical_room_code(item.preferred_room.code) in source_rooms]
                if matching:
                    siblings = matching
                    configurations = {tuple(getattr(item, field) for field in CONFIG_FIELDS) for item in siblings}
            if len(configurations) != 1:
                return None, 'Equivalent sections have conflicting CourseOffering configurations.', None
        configuration = dict(zip(CONFIG_FIELDS, next(iter(configurations))))
        source_periods = _source_periods(source_rows)
        warning = None
        if source_periods is None or source_periods != configuration['weekly_periods']:
            warning = f'Source has {source_periods} distinct teaching periods; equivalent offering requires {configuration["weekly_periods"]}. Check timetable completeness separately.'
        preferred_ids = {item.preferred_room_id for item in siblings}
        preferred = siblings[0].preferred_room if len(preferred_ids) == 1 else None
        if preferred and room_section_error(preferred, section, course):
            preferred = None
        configuration['preferred_room'] = preferred
        return configuration, None, warning

    # Without a sibling, only a single activity type with one-period source
    # blocks and matching Course contact hours provides enough information.
    hours = {
        'LECTURE': course.lecture_hours,
        'TUTORIAL': course.tutorial_hours,
        'PRACTICAL': course.practical_hours,
    }
    active_types = [(kind, count) for kind, count in hours.items() if count]
    source_periods = _source_periods(source_rows)
    if len(active_types) == 1 and source_periods == active_types[0][1] and all(
        _text(row.get('block_length')) in ('', '1', '1.0') for row in source_rows
    ):
        kind, periods = active_types[0]
        if kind != 'PRACTICAL' or periods == 1:
            return {
                'weekly_periods': periods, 'default_class_type': kind,
                'required_block_size': 1, 'allow_remainder_period': False,
                'room_type_requirement': '', 'preferred_room': None,
            }, None, None
    return None, 'No equivalent offering; Course contact hours and source placements do not determine a safe configuration.', None


def _faculty_roles(rows, faculty_by_code, sibling_offerings):
    found = {}
    problems = []
    for row in rows:
        employee = _text(row.get('employee_code'))
        faculty = faculty_by_code.get(employee.casefold())
        if not faculty:
            problems.append(f'Faculty Employee Code {employee or "(blank)"} was not found.')
            continue
        name = _text(row.get('faculty_name'))
        canonical_name = _faculty_display_name(faculty)
        if name and not faculty_name_matches(name, canonical_name):
            problems.append(f'Faculty Name {name!r} does not match {employee} ({canonical_name}).')
            continue
        explicit = _text(row.get('faculty_role')).upper()
        if explicit and explicit not in VALID_ROLES:
            problems.append(f'Unsupported Faculty Role {explicit!r} for {employee}.')
            continue
        if faculty.pk in found and explicit and found[faculty.pk][1] and found[faculty.pk][1] != explicit:
            problems.append(f'Conflicting Faculty Roles for {employee}.')
            continue
        previous = found.get(faculty.pk)
        found[faculty.pk] = (faculty, explicit or (previous[1] if previous else ''))
    if problems:
        return None, sorted(set(problems))
    if len(found) == 1:
        faculty, role = next(iter(found.values()))
        return [(faculty, role or 'PRIMARY')], []
    if not found:
        return None, ['No canonical faculty could be resolved.']
    inherited = {}
    for offering in sibling_offerings:
        for assignment in offering.faculties.all():
            inherited.setdefault(assignment.faculty_id, set()).add(assignment.role)
    assignments = []
    for faculty, role in found.values():
        if not role and len(inherited.get(faculty.pk, ())) == 1:
            role = next(iter(inherited[faculty.pk]))
        if not role:
            problems.append(f'Multiple faculty: role for {faculty.employee_code} needs Faculty Role in the source or a consistent sibling mapping.')
        else:
            assignments.append((faculty, role))
    if assignments and sum(role == 'PRIMARY' for _, role in assignments) != 1:
        problems.append('Multiple faculty must have exactly one PRIMARY role.')
    return (None, problems) if problems else (assignments, [])


def plan_repair(version, source_rows):
    if version.status != TimetableVersion.Status.DRAFT:
        raise ValueError('Repair requires a DRAFT timetable version.')
    semester = version.timetable.semester
    if semester.session_id != version.timetable.academic_session_id:
        raise ValueError('Timetable semester and academic session do not agree.')
    rows = _rows(source_rows)
    required = {'section', 'course_code', 'employee_code', 'day', 'time_slot', 'room'}
    if not rows or required - set(rows[0]):
        raise ValueError('Missing required timetable columns: ' + ', '.join(sorted(required - set(rows[0]) if rows else required)))

    sections = defaultdict(list)
    for section in Section.objects.filter(semester=semester).select_related('program'):
        sections[section.name.casefold()].append(section)
    courses = {course.code.casefold(): course for course in Course.objects.all()}
    faculty_by_code = {faculty.employee_code.casefold(): faculty for faculty in Faculty.objects.select_related('user') if faculty.employee_code}
    offerings = defaultdict(list)
    for offering in CourseOffering.objects.filter(semester=semester).select_related('section__program', 'course'):
        offerings[(offering.section_id, offering.course_id)].append(offering)

    groups = {}
    problems = []
    for number, row in enumerate(rows, 2):
        section_name, course_code = _text(row.get('section')), _text(row.get('course_code'))
        section_matches = sections.get(section_name.casefold(), [])
        course = courses.get(course_code.casefold())
        if len(section_matches) != 1 or not course:
            problems.append(f'Row {number}: Section {section_name!r} is missing/ambiguous or Course {course_code!r} is missing.')
            continue
        section = section_matches[0]
        key = (section.pk, course.pk)
        group = groups.setdefault(key, {'section': section, 'course': course, 'rows': [], 'row_numbers': [], 'existing': offerings.get(key, [])})
        group['rows'].append(row)
        group['row_numbers'].append(number)

    prepared = []
    warnings = []
    for group in groups.values():
        section, course, existing = group['section'], group['course'], group['existing']
        label = f'{semester.name} / {section.name} / {course.code}'
        if len(existing) > 1:
            problems.append(f'{label}: duplicate canonical CourseOfferings already exist.')
            continue
        for row in group['rows']:
            imported_credit = _text(row.get('credit'))
            if imported_credit:
                try:
                    value = Decimal(imported_credit)
                    if not value.is_finite() or value < 0 or value != course.credit:
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    problems.append(f'{label}: invalid/mismatched Credit {imported_credit!r}.')
        if any(problem.startswith(f'{label}: invalid/mismatched Credit') for problem in problems):
            continue
        siblings = list(CourseOffering.objects.filter(
            semester=semester, course=course, section__program_id=section.program_id,
            section__year=section.year, active=True,
        ).exclude(section=section).prefetch_related('faculties'))
        faculty_roles, faculty_problems = _faculty_roles(group['rows'], faculty_by_code, siblings)
        if faculty_problems:
            problems.extend(f'{label}: {message}' for message in faculty_problems)
            continue
        if existing:
            configuration = None
            existing_faculty = set(existing[0].faculties.values_list('faculty_id', flat=True))
            missing_assignments = [(faculty, role) for faculty, role in faculty_roles if faculty.pk not in existing_faculty]
        else:
            configuration, issue, warning = _configuration(semester, section, course, group['rows'])
            if issue:
                problems.append(f'{label}: {issue}')
                continue
            if warning:
                warnings.append(f'{label}: {warning}')
            missing_assignments = faculty_roles
        prepared.append({**group, 'configuration': configuration, 'faculty_assignments': missing_assignments})

    missing = sum(not group['existing'] for group in groups.values())
    return {
        'semester': semester, 'source_rows': rows, 'plans': prepared,
        'missing_found': missing,
        'already_existing': len(groups) - missing,
        'would_create': sum(not item['existing'] for item in prepared),
        'faculty_assignments_to_create': sum(len(item['faculty_assignments']) for item in prepared),
        'existing_offering_mappings_to_create': sum(len(item['faculty_assignments']) for item in prepared if item['existing']),
        'problems': problems, 'warnings': warnings,
    }


def apply_repair(version, plan):
    counts = {'offerings_created': 0, 'faculty_mappings_created': 0, 'existing_offering_mappings_created': 0}
    with transaction.atomic():
        locked_version = TimetableVersion.objects.select_for_update().get(pk=version.pk)
        if locked_version.status != TimetableVersion.Status.DRAFT:
            raise ValueError('Repair requires a DRAFT timetable version.')
        for item in plan['plans']:
            existing = list(CourseOffering.objects.select_for_update().filter(
                semester=plan['semester'], section=item['section'], course=item['course'],
            ))
            if len(existing) > 1:
                raise ValueError(f'Duplicate CourseOfferings appeared for {item["course"].code} / {item["section"].name}.')
            if bool(existing) != bool(item['existing']):
                raise ValueError(f'CourseOffering state changed for {item["course"].code} / {item["section"].name}; rerun the audit.')
            if existing:
                offering = existing[0]
            else:
                offering = CourseOffering.objects.create(
                    semester=plan['semester'], section=item['section'], course=item['course'],
                    **item['configuration'],
                )
                counts['offerings_created'] += 1
            for faculty, role in item['faculty_assignments']:
                _, created = CourseOfferingFaculty.objects.get_or_create(
                    course_offering=offering, faculty=faculty,
                    defaults={'role': role, 'priority': 1},
                )
                if created:
                    counts['faculty_mappings_created'] += 1
                    if item['existing']:
                        counts['existing_offering_mappings_created'] += 1
    return counts


def preview_counts(version, source_rows):
    result, _ = parse(version, source_rows)
    return {
        'total': result['total'], 'valid': result['valid'], 'invalid': result['invalid'],
        'missing_offering_rows': sum('No Course Offering found' in error['message'] for error in result['errors']),
    }
