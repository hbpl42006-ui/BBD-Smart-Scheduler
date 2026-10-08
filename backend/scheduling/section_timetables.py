import re
from collections import defaultdict

from django.http import HttpResponse
from django.utils.text import slugify
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role
from academics.models import Section
from common.models import TimeSlot
from scheduling.models import ScheduleEntry, TimetableVersion
from scheduling.serializers import ScheduleEntrySerializer


MANAGER_ROLE = Role.HOD_OR_DEAN_APPROVER
WEEKDAYS = dict(ScheduleEntry.Weekday.choices)


def _managed_sections(user):
    if user.is_superuser or user.role == Role.SUPER_ADMIN:
        return Section.objects.all()
    if not _valid_manager_scope(user):
        return Section.objects.none()
    departments = user.managed_departments.all()
    if user.management_scope == user.ManagementScope.HOD and departments.count() != 1:
        return Section.objects.none()
    if user.management_scope == user.ManagementScope.DEAN and not departments.exists():
        return Section.objects.none()
    return Section.objects.filter(program__department__in=departments)


def _valid_manager_scope(user):
    if user.role != MANAGER_ROLE or not user.management_scope:
        return False
    department_count = user.managed_departments.count()
    if user.management_scope == user.ManagementScope.HOD:
        return department_count == 1
    return user.management_scope == user.ManagementScope.DEAN and department_count > 0


def _can_manage(request):
    user = request.user
    return bool(user.is_superuser or user.role in {Role.SUPER_ADMIN, MANAGER_ROLE})


def _coordinator(section):
    user = section.coordinator
    faculty = getattr(user, 'faculty_profile', None) if user else None
    user_name = f'{user.first_name} {user.last_name}'.strip() if user else ''
    return {
        'name': section.coordinator_name or (faculty.name if faculty and faculty.name else '') or user_name,
        'mobile': section.coordinator_mobile or (user.whatsapp_number if user else ''),
        'user_id': str(user.pk) if user else None,
    }


def _section_query():
    return Section.objects.select_related(
        'program__department__institution', 'semester__session', 'coordinator',
    ).prefetch_related('coordinator__faculty_profile', 'weekly_off_policies')


def _matching_timetable_versions(section, version_id=None):
    query = TimetableVersion.objects.select_related(
        'timetable__institution', 'timetable__department', 'timetable__semester',
        'timetable__academic_session',
    ).filter(
        timetable__department_id=section.program.department_id,
        timetable__semester_id=section.semester_id,
        timetable__academic_session_id=section.semester.session_id,
        timetable__active=True,
    )
    if version_id:
        query = query.filter(pk=version_id)
    return query.order_by('-published_at', '-version_no')


def _version_payload(version):
    return {
        'id': str(version.pk), 'version_no': version.version_no,
        'status': version.status, 'created_at': version.created_at,
        'published_at': version.published_at,
        'timetable_id': str(version.timetable_id), 'title': version.timetable.title,
    }


def _detail_data(section, version_id=None):
    versions = list(_matching_timetable_versions(section))
    selected = None
    if version_id:
        selected = next((version for version in versions if str(version.pk) == str(version_id)), None)
        if selected is None:
            return None
    else:
        selected = next((version for version in versions if version.status == TimetableVersion.Status.PUBLISHED), None)
    entries = []
    if selected:
        rows = list(ScheduleEntry.objects.filter(version=selected, section=section).select_related(
            'section__program__department', 'section__semester__session',
            'course_offering__course', 'start_slot__template', 'room',
        ).prefetch_related('faculty_assignments__faculty__user').order_by('weekday', 'start_slot__order'))
        entries = ScheduleEntrySerializer(rows, many=True).data
    template_id = next((entry.start_slot.template_id for entry in ScheduleEntry.objects.filter(
        version=selected, section=section,
    ).select_related('start_slot')[:1]), None) if selected else None
    if template_id is None:
        template_id = TimeSlot.objects.filter(
            template__institution_id=section.program.department.institution_id,
        ).order_by('order').values_list('template_id', flat=True).first()
    slots = list(TimeSlot.objects.filter(template_id=template_id).order_by('order')) if template_id else []
    return {
        'section': {
            'id': str(section.pk), 'name': section.name, 'year': section.year,
            'weekly_off_day': _weekly_off_day(section),
            'student_strength': section.student_strength,
            'program': {'id': str(section.program_id), 'code': section.program.code, 'name': section.program.name},
            'department': {'id': str(section.program.department_id), 'code': section.program.department.code,
                           'name': section.program.department.name},
            'institution': {'id': str(section.program.department.institution_id),
                            'name': section.program.department.institution.name},
            'session': {'id': str(section.semester.session_id), 'name': section.semester.session.name},
            'semester': {'id': str(section.semester_id), 'name': section.semester.name,
                         'type': section.semester.type, 'number': section.semester.number},
            'coordinator': _coordinator(section),
        },
        'version': _version_payload(selected) if selected else None,
        'versions': [_version_payload(version) for version in versions],
        'time_slots': [{'id': str(slot.pk), 'label': slot.label, 'order': slot.order,
                        'is_break': slot.is_break, 'start_time': slot.start_time.strftime('%H:%M'),
                        'end_time': slot.end_time.strftime('%H:%M')} for slot in slots],
        'entries': entries,
    }


def _draw_pdf(payload):
    """Produce a single-page vector A4-landscape timetable PDF with core fonts."""
    import textwrap

    width, height = 842, 595
    commands = []

    def text(x, y, value, size=8, bold=False):
        value = str(value or '').encode('cp1252', 'replace').decode('cp1252')
        value = value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        font = 'F2' if bold else 'F1'
        commands.append(f'BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({value}) Tj ET')

    def line(x1, y1, x2, y2, color=(0.45, 0.49, 0.55), weight=0.6):
        commands.append(f'{color[0]} {color[1]} {color[2]} RG {weight} w {x1} {y1} m {x2} {y2} l S')

    def rect(x, y, w, h, fill=(1, 1, 1), stroke=(0.55, 0.58, 0.62)):
        commands.append(f'{fill[0]} {fill[1]} {fill[2]} rg {stroke[0]} {stroke[1]} {stroke[2]} RG 0.5 w {x} {y} {w} {h} re B')

    section = payload['section']
    version = payload.get('version') or {}
    text(24, 570, section['institution']['name'], 17, True)
    text(24, 552, section['department']['name'], 10, True)
    text(24, 532, 'SECTION TIMETABLE', 14, True)
    text(24, 514, f"Session: {section['session']['name']}    Semester: {section['semester']['name']}    Version: {version.get('status', 'No published timetable')} v{version.get('version_no', '-')}", 8)
    text(24, 500, f"Program: {section['program']['name']}    Year: {section['year']}    Section: {section['name']}", 8)
    text(24, 486, f"Class Coordinator: {section['coordinator']['name'] or 'Not assigned'}    Mobile: {section['coordinator']['mobile'] or 'Not assigned'}", 8)

    x0, y_top = 24, 459
    widths = [66, 148, 148, 148, 148, 148]
    header_h, row_h = 22, 44
    slots = payload['time_slots']
    starts = [slot for slot in slots if not slot['is_break']]
    grid_rows = []
    for slot in slots:
        grid_rows.append(slot)
    if len(grid_rows) > 9:
        row_h = 34
    headers = ['Time', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    x = x0
    for index, label in enumerate(headers):
        rect(x, y_top - header_h, widths[index], header_h, fill=(0.94, 0.96, 0.98))
        text(x + 5, y_top - 15, label, 8, True)
        x += widths[index]
    row_positions = {}
    y = y_top - header_h
    for index, slot in enumerate(grid_rows):
        bottom = y - row_h
        row_positions[slot['id']] = (bottom, row_h, index)
        if slot['is_break']:
            rect(x0, bottom, sum(widths), row_h, fill=(0.97, 0.97, 0.97))
            text(x0 + 5, bottom + row_h / 2 - 3, 'LUNCH / BREAK', 8, True)
        else:
            x = x0
            rect(x, bottom, widths[0], row_h)
            text(x + 4, bottom + row_h / 2 - 3, slot['label'], 7, True)
            x += widths[0]
            for _day in range(5):
                rect(x, bottom, widths[1], row_h)
                x += widths[1]
        y = bottom

    slot_index = {slot['id']: idx for idx, slot in enumerate(grid_rows)}
    by_day = defaultdict(list)
    for entry in payload['entries']:
        by_day[entry['weekday']].append(entry)
    day_width = widths[1]
    for weekday in range(5):
        for entry in by_day[weekday]:
            if entry.get('start_slot') not in row_positions:
                continue
            start_index = slot_index[entry['start_slot']]
            bottom, _height, _ = row_positions[entry['start_slot']]
            block_length = max(1, int(entry.get('block_length') or 1))
            covered = grid_rows[start_index:start_index + block_length]
            block_height = sum(row_positions[slot['id']][1] for slot in covered if slot['id'] in row_positions)
            x = x0 + widths[0] + weekday * day_width
            rect(x + 1, bottom + 1, day_width - 2, block_height - 2, fill=(0.97, 0.98, 0.99), stroke=(0.62, 0.66, 0.71))
            faculty_names = ', '.join(item.get('name', '') for item in entry.get('faculty_assignments', []))
            room = 'ONLINE' if entry.get('delivery_mode') == 'ONLINE' else f"OFFLINE - Room {entry.get('room_code') or 'pending'}"
            lines = [entry.get('course', {}).get('code', ''), entry.get('course', {}).get('name', ''),
                     f"{entry.get('entry_type', '')}{' | OFFICIAL FIXED' if entry.get('locked') else ''}",
                     f'{faculty_names} | {room}']
            max_lines = max(2, int((block_height - 5) // 5.7))
            content_y = bottom + block_height - 7
            for line_text in lines:
                for part in textwrap.wrap(str(line_text), width=39) or ['']:
                    if content_y < bottom + 2 or max_lines <= 0:
                        break
                    text(x + 4, content_y, part, 5.4, line_text == lines[0])
                    content_y -= 5.7
                    max_lines -= 1
                if max_lines <= 0:
                    break

    text(24, 31, 'ONLINE = Online Class | Room XXX = Offline / Physical Class', 7)
    text(24, 20, 'Official Fixed entries are locked. Multi-period classes span their full consecutive time block.', 7)
    stream = '\n'.join(commands).encode('cp1252', 'replace')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
    ]
    pdf = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(pdf)
    pdf.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(pdf)


def _draw_official_pdf(payload):
    """Draw the official one-page BBDU timetable sheet as vector PDF content."""
    width, height = 842, 595
    commands = []

    def color_fill(color):
        commands.append(f'{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg')

    def color_stroke(color=(0, 0, 0), weight=0.55):
        commands.append(f'{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG {weight} w')

    def text(x, y, value, size=8, bold=False, rotate=False):
        value = str(value or '').encode('cp1252', 'replace').decode('cp1252')
        value = value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        font = 'F2' if bold else 'F1'
        if rotate:
            commands.append(f'BT /{font} {size} Tf 0 1 -1 0 {x:.1f} {y:.1f} Tm ({value}) Tj ET')
        else:
            commands.append(f'BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({value}) Tj ET')

    def rect(x, y, w, h, fill=(1, 1, 1), stroke=(0, 0, 0)):
        color_fill(fill)
        color_stroke(stroke)
        commands.append(f'{x:.2f} {y:.2f} {w:.2f} {h:.2f} re B')

    section = payload['section']
    version = payload.get('version') or {}
    entries = payload.get('entries') or []
    slots = payload.get('time_slots') or []
    yellow = (1.0, 0.90, 0.0)
    text(width / 2 - 126, 572, section['institution']['name'], 17, True)
    text(width / 2 - 67, 554, 'School of Engineering', 11, True)
    text(width / 2 - 139, 539, section['department']['name'], 10, True)
    year_names = {1: 'First', 2: 'Second', 3: 'Third', 4: 'Fourth'}
    if section['program'].get('code', '').startswith('MTECH') or section['program']['name'].startswith('M.Tech'):
        semester_number = section['semester'].get('number') or 1
        roman = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI', 7: 'VII', 8: 'VIII'}.get(semester_number, str(semester_number))
        program_heading = f"{section['program']['name']} - {roman} Semester"
    else:
        program_heading = f"{section['program']['name']} {year_names.get(section['year'], section['year'])} Year, {section['semester']['name']}"
    text(width / 2 - min(170, len(program_heading) * 2.5), 521, program_heading, 10, True)
    text(width / 2 - 65, 507, f"Academic Session: {section['session']['name']}", 9, True)
    if version:
        text(690, 507, f"{version.get('status', '')} v{version.get('version_no', '')}", 7)

    left, right, top = 18, 824, 493
    section_w, day_w, head_h, row_h = 24, 34, 23, 35
    slot_w = (right - left - section_w - day_w) / max(1, len(slots))
    grid_bottom = top - head_h - row_h * 5
    rect(left, top - head_h, section_w, head_h)
    rect(left, grid_bottom, section_w, row_h * 5)
    rect(left + section_w, top - head_h, day_w, head_h, yellow)
    text(left + section_w + 2, top - 15, 'Time/Day', 6.5, True)
    for col, slot in enumerate(slots):
        x = left + section_w + day_w + col * slot_w
        rect(x, top - head_h, slot_w, head_h, yellow)
        if not slot.get('is_break'):
            sh = int(slot.get('start_time', '00:00')[:2])
            eh = int(slot.get('end_time', '00:00')[:2])
            sh_label = str(sh if sh <= 12 else sh - 12).zfill(2) if sh <= 12 else str(sh - 12)
            eh_label = str(eh if eh <= 12 else eh - 12).zfill(2) if eh <= 12 else str(eh - 12)
            label = f'{sh_label} to {eh_label}'
            text(x + max(2, (slot_w - len(label) * 3.4) / 2), top - 15, label, 6.5, True)

    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    by_day = defaultdict(list)
    for entry in entries:
        by_day[int(entry['weekday'])].append(entry)
    slot_index = {str(slot['id']): index for index, slot in enumerate(slots)}
    for day, day_label in enumerate(days):
        y = top - head_h - (day + 1) * row_h
        rect(left + section_w, y, day_w, row_h, yellow)
        text(left + section_w + 8, y + row_h / 2 - 2, day_label, 7, True)
        for col, slot in enumerate(slots):
            x = left + section_w + day_w + col * slot_w
            if slot.get('is_break'):
                rect(x, y, slot_w, row_h, (1, 1, 1))
                text(x + slot_w / 2 - 2, y + row_h / 2 - 3, 'LUNCH'[day], 9, True)
                continue
            occupying = [item for item in by_day[day] if str(item.get('start_slot')) == str(slot['id'])]
            if not occupying:
                is_covered = any(
                    slot_index.get(str(item.get('start_slot')), -100) < col < slot_index.get(str(item.get('start_slot')), -100) + int(item.get('block_length') or 1)
                    for item in by_day[day]
                )
                if not is_covered:
                    rect(x, y, slot_w, row_h)
                continue
            entry = occupying[0]
            block = max(1, min(int(entry.get('block_length') or 1), len(slots) - col))
            rect(x, y, slot_w * block, row_h)
            code = _compact_metadata(entry, include_room=True)
            text(x + 3, y + row_h / 2 - 2.5, code[:int(max(5, slot_w * block / 4.5))], 6.2, True)

    # Left-side vertically oriented merged label.
    semester_number = section['semester'].get('number') or (section['year'] * 2 if section['semester'].get('type') == 'EVEN' else section['year'] * 2 - 1)
    roman_semester = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI', 7: 'VII', 8: 'VIII'}.get(semester_number, str(semester_number))
    vertical = f"{section['program']['name']} - {roman_semester} Sem - Section: {section['name']}"
    text(left + 8, grid_bottom + 5, vertical[:65], 7, True, rotate=True)

    coord_y = grid_bottom - 18
    rect(left, coord_y, right - left, 18, yellow)
    color_stroke()
    commands.append(f'{left} {coord_y} {right-left} 18 re S')
    text(left + 7, coord_y + 6, f"Class Coordinator: {section['coordinator']['name'] or 'Not Assigned'}", 7.5, True)
    mobile = section['coordinator']['mobile'] or '-'
    text(right - 180, coord_y + 6, f'Mobile No.: {mobile}', 7.5, True)

    grouped = {}
    for entry in entries:
        key = entry.get('course_offering')
        if key not in grouped:
            grouped[key] = {'entry': entry, 'metadata': [], 'faculty': []}
        metadata = _compact_metadata(entry, include_room=False)
        if metadata and metadata not in grouped[key]['metadata']:
            grouped[key]['metadata'].append(metadata)
        for assignment in entry.get('faculty_assignments', []):
            if assignment.get('name') and assignment['name'] not in grouped[key]['faculty']:
                grouped[key]['faculty'].append(assignment['name'])

    course_top = coord_y - 5
    header_h, course_h = 15, max(5, min(17, (course_top - 57) / max(1, len(grouped))))
    widths = [42, 84, 270, 130, right - left - 42 - 84 - 270 - 130]
    headers = ['Credit', 'Codes', 'Course Name', 'Meta Data', 'Faculty Name']
    positions = [left]
    for column_w in widths:
        positions.append(positions[-1] + column_w)
    x = left
    for index, title in enumerate(headers):
        rect(x, course_top - header_h, widths[index], header_h, yellow)
        text(x + 3, course_top - 10, title, 7, True)
        x += widths[index]
    y = course_top - header_h
    for item in grouped.values():
        y -= course_h
        entry = item['entry']
        course = entry.get('course') or {}
        values = [course.get('credit') if course.get('credit') is not None else '-', course.get('code') or '-', course.get('name') or '-', '; '.join(item['metadata']) or '-', ', '.join(item['faculty']) or '-']
        for index, value in enumerate(values):
            rect(positions[index], y, widths[index], course_h)
            max_chars = max(5, int(widths[index] / 4.2))
            text(positions[index] + 3, y + course_h / 2 - 2.5, str(value)[:max_chars], min(6.1, max(4.0, course_h * 0.48)))

    legend_y = max(42, y - 12)
    text(left, legend_y, 'No Room No. = ONLINE  |  Room No. shown = OFFLINE / Physical Class', 7)
    footer_y = max(17, legend_y - 26)
    text(left + 10, footer_y, 'Head / HOD - Department', 7)
    text(width / 2 - 65, footer_y, 'Coordinator - Academic Activities', 7)
    text(right - 120, footer_y, 'Dean - School of Engineering', 7)

    stream = '\n'.join(commands).encode('cp1252', 'replace')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding /WinAnsiEncoding >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Times-Bold /Encoding /WinAnsiEncoding >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
    ]
    pdf = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(pdf)
    pdf.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(pdf)


def _compact_metadata(entry, include_room=False):
    if entry.get('entry_type') == 'LIBRARY':
        return 'LIB'
    course = entry.get('course') or {}
    activity = entry.get('entry_type', '')
    prefix = 'L' if activity == 'LECTURE' else 'P' if activity in ('PRACTICAL', 'LAB') else activity
    initials = '+'.join(dict.fromkeys(item.get('initials', '') for item in entry.get('faculty_assignments', []) if item.get('initials')))
    parts = [prefix, course.get('short_code') or course.get('code'), initials]
    if include_room and entry.get('delivery_mode') != 'ONLINE' and entry.get('room_code'):
        parts.append(entry['room_code'])
    return '/'.join(str(part) for part in parts if part)


class SectionTimetableList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can_manage(request):
            return Response({'detail': 'You do not have permission to view section timetables.'}, status=403)
        if not (request.user.is_superuser or request.user.role == Role.SUPER_ADMIN) and not _valid_manager_scope(request.user):
            return Response({'detail': 'An HOD/Dean scope and authorized department assignment are required.'}, status=403)
        queryset = _managed_sections(request.user).select_related(
            'program__department__institution', 'semester__session', 'coordinator',
        ).prefetch_related('weekly_off_policies')
        filters = {
            'session': 'semester__session_id', 'semester': 'semester_id',
            'department': 'program__department_id', 'program': 'program_id', 'year': 'year',
            'section': 'id',
        }
        for key, field in filters.items():
            value = request.query_params.get(key)
            if value:
                queryset = queryset.filter(**{field: value})
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                models_q_name(search)
            )
        sections = list(queryset.order_by('semester__session__name', 'semester__number', 'program__name', 'year', 'name'))
        departments = {str(section.program.department_id) for section in sections}
        semesters = {str(section.semester_id) for section in sections}
        version_rows = TimetableVersion.objects.select_related('timetable__department', 'timetable__semester').filter(
            timetable__department_id__in=departments,
            timetable__semester_id__in=semesters,
            timetable__active=True,
        ).order_by('-published_at', '-version_no')
        version_map = defaultdict(list)
        for version in version_rows:
            version_map[(str(version.timetable.department_id), str(version.timetable.semester_id))].append(version)
        requested_version = request.query_params.get('version')
        requested_status = request.query_params.get('status')
        output = []
        for section in sections:
            available = version_map[(str(section.program.department_id), str(section.semester_id))]
            versions = [v for v in available if (not requested_version or str(v.pk) == requested_version)
                        and (not requested_status or v.status == requested_status)]
            if (requested_version or requested_status) and not versions:
                continue
            published = next((v for v in available if v.status == TimetableVersion.Status.PUBLISHED), None)
            selected = next((v for v in versions if v.status == TimetableVersion.Status.PUBLISHED), None)
            selected = selected or (versions[0] if requested_version or requested_status else published)
            output.append({
                'id': str(section.pk), 'name': section.name, 'year': section.year,
                'weekly_off_day': _weekly_off_day(section),
                'program': {'id': str(section.program_id), 'code': section.program.code, 'name': section.program.name},
                'department': {'id': str(section.program.department_id), 'code': section.program.department.code, 'name': section.program.department.name},
                'session': {'id': str(section.semester.session_id), 'name': section.semester.session.name},
                'semester': {'id': str(section.semester_id), 'name': section.semester.name, 'type': section.semester.type},
                'coordinator': _coordinator(section),
                'version': _version_payload(selected) if selected else None,
                'versions': [_version_payload(v) for v in available],
            })
        return Response(output)


def models_q_name(search):
    from django.db.models import Q
    query = (Q(name__icontains=search) | Q(program__name__icontains=search) |
             Q(program__code__icontains=search) | Q(program__department__name__icontains=search))
    return query | Q(year=int(search)) if search.isdigit() else query


def _weekly_off_day(section):
    from academics.weekly_off import get_section_weekly_off_policy
    policy = get_section_weekly_off_policy(section, section.semester.session, section.semester)
    return policy.weekday if policy else None


class SectionTimetableDetail(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, section_id):
        if not _can_manage(request):
            return Response({'detail': 'You do not have permission to view section timetables.'}, status=403)
        if not (request.user.is_superuser or request.user.role == Role.SUPER_ADMIN) and not _valid_manager_scope(request.user):
            return Response({'detail': 'An HOD/Dean scope and authorized department assignment are required.'}, status=403)
        section = _section_query().filter(pk=section_id).first()
        if not section:
            return Response({'detail': 'Section not found.'}, status=404)
        if not _managed_sections(request.user).filter(pk=section.pk).exists():
            return Response({'detail': 'This section is outside your authorized department scope.'}, status=403)
        payload = _detail_data(section, request.query_params.get('version'))
        if payload is None:
            return Response({'detail': 'The selected timetable version is not available for this section.'}, status=404)
        return Response(payload)


class SectionCoordinatorUpdate(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, section_id):
        if not _can_manage(request):
            return Response({'detail': 'You do not have permission to update section coordinators.'}, status=403)
        if not (request.user.is_superuser or request.user.role == Role.SUPER_ADMIN) and not _valid_manager_scope(request.user):
            return Response({'detail': 'An HOD/Dean scope and authorized department assignment are required.'}, status=403)
        section = _section_query().filter(pk=section_id).first()
        if not section:
            return Response({'detail': 'Section not found.'}, status=404)
        if not _managed_sections(request.user).filter(pk=section.pk).exists():
            return Response({'detail': 'This section is outside your authorized department scope.'}, status=403)
        name = str(request.data.get('name', '')).strip()
        mobile = str(request.data.get('mobile', '')).strip()
        if len(name) > 255:
            return Response({'mobile': 'Coordinator name must be 255 characters or fewer.'}, status=400)
        if mobile and (len(mobile) > 25 or not re.fullmatch(r'\+?[0-9][0-9\s().-]{6,23}', mobile)):
            return Response({'mobile': 'Enter a valid phone number (digits with an optional country code).'}, status=400)
        section.coordinator_name = name
        section.coordinator_mobile = mobile
        section.save(update_fields=['coordinator_name', 'coordinator_mobile', 'updated_at'])
        return Response({'coordinator': _coordinator(section)})


class SectionTimetablePdf(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, section_id):
        if not _can_manage(request):
            return Response({'detail': 'You do not have permission to download section timetables.'}, status=403)
        if not (request.user.is_superuser or request.user.role == Role.SUPER_ADMIN) and not _valid_manager_scope(request.user):
            return Response({'detail': 'An HOD/Dean scope and authorized department assignment are required.'}, status=403)
        section = _section_query().filter(pk=section_id).first()
        if not section:
            return Response({'detail': 'Section not found.'}, status=404)
        if not _managed_sections(request.user).filter(pk=section.pk).exists():
            return Response({'detail': 'This section is outside your authorized department scope.'}, status=403)
        payload = _detail_data(section, request.query_params.get('version'))
        if payload is None:
            return Response({'detail': 'The selected timetable version is not available for this section.'}, status=404)
        if payload['version'] is None:
            return Response({'detail': 'No published timetable is available for this section.'}, status=404)
        content = _draw_official_pdf(payload)
        section_part = slugify(section.name).upper()
        semester_part = slugify(section.semester.name).title()
        session_part = slugify(section.semester.session.name)
        filename = f'BBDU_{section_part}_{semester_part}_{session_part}_Timetable' if section_part and semester_part and session_part else 'BBDU_Section_Timetable'
        response = HttpResponse(content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
        return response
