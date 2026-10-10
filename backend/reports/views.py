import csv
from io import BytesIO, StringIO
from openpyxl import Workbook
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from common.permissions import RolePermission
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter, OpenApiTypes
from rest_framework import serializers
from .services import reporting
from .serializers import ReportRowSerializer
from faculty.models import Faculty
from rooms.models import Room
from common.models import TimeSlot
from uuid import UUID


def _validate_schedule_scope(request):
    from scheduling.services.temporary_schedules import parse_schedule_scope
    try:
        mode, _ = parse_schedule_scope(request)
    except ValueError as error:
        return Response({'detail': str(error)}, status=400)
    if mode in ('effective', 'special') and (request.query_params.get('version') or request.query_params.get('version_id')):
        version = reporting._version(request)
        if version and version.status != 'PUBLISHED':
            return Response({'detail': 'Effective and special schedules require a published timetable version.'}, status=400)
    return None


def _faculty_display_name(faculty):
    linked = f'{faculty.user.first_name} {faculty.user.last_name}'.strip() if faculty.user_id else ''
    return linked or faculty.name or faculty.initials or faculty.employee_code or ''


class FacultyWorkloadOptionsView(APIView):
    permission_classes=[IsAuthenticated]

    @extend_schema(responses=inline_serializer(name='FacultyWorkloadOption', many=True, fields={
        'id': serializers.UUIDField(), 'name': serializers.CharField(),
    }))
    def get(self, request):
        rows = Faculty.objects.filter(active=True).select_related('user').only(
            'id', 'name', 'initials', 'employee_code', 'user__first_name', 'user__last_name',
        )
        if request.query_params.get('department'):
            rows = rows.filter(department_id=request.query_params['department'])
        if request.user.role == 'FACULTY':
            profile = getattr(request.user, 'faculty_profile', None)
            rows = rows.filter(pk=profile.pk) if profile else rows.none()
        options = [{'id': str(f.pk), 'name': _faculty_display_name(f)} for f in rows]
        return Response(sorted(options, key=lambda item: (item['name'].casefold(), item['id'])))

MANAGEMENT = {'SUPER_ADMIN','ACADEMIC_ADMIN','HOD_OR_DEAN_APPROVER','TIMETABLE_COORDINATOR'}

def export_response(name, rows, fmt, fieldnames=None):
    if fmt == 'csv':
        output=StringIO(); fields=fieldnames or sorted({k for row in rows for k in row if not isinstance(row.get(k), (list,dict,set))}); writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader();writer.writerows([{k:row.get(k,'') for k in fields} for row in rows]);return HttpResponse(output.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{name}.csv"'})
    if fmt == 'xlsx':
        wb=Workbook();ws=wb.active;ws.title=name[:31];fields=fieldnames or sorted({k for row in rows for k in row if not isinstance(row.get(k),(list,dict,set))});ws.append(fields)
        for row in rows: ws.append([row.get(k,'') for k in fields])
        from copy import copy
        for cell in ws[1]:
            font=copy(cell.font);font.bold=True;cell.font=font
        for column in ws.columns: ws.column_dimensions[column[0].column_letter].width=min(max(max(len(str(x.value or '')) for x in column)+2,12),40)
        stream=BytesIO();wb.save(stream);return HttpResponse(stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{name}.xlsx"'})
    return None

class ReportView(APIView):
    permission_classes=[IsAuthenticated]
    report_name='report'; builder=None
    @extend_schema(description='Data-derived report. Use export=csv or export=xlsx for file output.', parameters=[OpenApiParameter('export',OpenApiTypes.STR,OpenApiParameter.QUERY,enum=['csv','xlsx']),OpenApiParameter('version',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('section',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('faculty',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('room',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('semester',OpenApiTypes.UUID,OpenApiParameter.QUERY)], responses=ReportRowSerializer(many=True))
    def get(self,request):
        if self.report_name in ('section-timetable','faculty-timetable','room-timetable','free-rooms'):
            invalid = _validate_schedule_scope(request)
            if invalid: return invalid
        if request.user.role == 'FACULTY' and self.report_name not in ('faculty-timetable','faculty-workload'): return Response({'detail':'You do not have permission to access this report.'},403)
        if request.user.role == 'FACULTY' and self.report_name=='faculty-workload' and request.query_params.get('faculty') and str(request.query_params['faculty']) != str(getattr(getattr(request.user,'faculty_profile',None),'pk',None)): return Response({'detail':'You may only view your own workload.'},403)
        rows=self.builder(request); fmt=(request.query_params.get('export') or request.query_params.get('format','')).lower(); exported=export_response(self.report_name,rows,fmt) if isinstance(rows,list) and fmt in ('csv','xlsx') else None
        return exported or Response(rows)

class FacultyWorkloadView(ReportView): report_name='faculty-workload'; builder=staticmethod(reporting.faculty_workload)
class RoomUtilizationView(ReportView): report_name='room-utilization'; builder=staticmethod(reporting.room_utilization)
class SectionTimetableReportView(ReportView):
    report_name = 'section-timetable'
    builder = staticmethod(reporting.section_timetable)

    def get(self, request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        invalid = _validate_section_timetable_filters(request)
        if invalid:
            return invalid
        fmt = (request.query_params.get('export') or request.query_params.get('format', '')).lower()
        if fmt in ('csv', 'xlsx'):
            source = self.builder(request)
            fields = (('Section', 'section'), ('Program', 'program'), ('Year', 'year'), ('Semester', 'semester'),
                ('Academic Session', 'session'), ('Day', 'day'), ('Start Time', 'start_time'), ('End Time', 'end_time'),
                ('Course Code', 'course_code'), ('Course Name', 'course_name'), ('Faculty', 'faculty_names'),
                ('Room', 'room'), ('Delivery Mode', 'delivery_mode'), ('Block Length', 'block_length'))
            rows = [{label: row.get(key, '') for label, key in fields} for row in source]
            return export_response(self.report_name, rows, fmt, [label for label, _ in fields])
        return super().get(request)


class SectionTimetableVisualView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        invalid = _validate_section_timetable_filters(request)
        if invalid:
            return invalid
        version = reporting._version(request)
        section_id = request.query_params.get('section')
        if section_id and not reporting._filtered_section_candidates(request, version, include_section=False).filter(pk=section_id).exists():
            return Response({'detail': 'The selected section is outside the current timetable filter scope.'}, status=400)
        return Response(reporting.section_timetable_visual(request))


class SectionTimetableOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        return Response(reporting.section_timetable_options(request))
class FacultyTimetableReportView(ReportView):
    report_name = 'faculty-timetable'
    builder = staticmethod(reporting.faculty_timetable)

    def get(self, request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        invalid = _validate_faculty_timetable_filters(request)
        if invalid:
            return invalid
        if request.user.role == 'FACULTY':
            profile = getattr(request.user, 'faculty_profile', None)
            if not profile:
                return Response({'detail': 'No faculty profile is linked to this account.'}, status=403)
            if request.query_params.get('faculty') and str(request.query_params['faculty']) != str(profile.pk):
                return Response({'detail': 'You may only view your own timetable.'}, status=403)
        fmt = (request.query_params.get('export') or request.query_params.get('format', '')).lower()
        if fmt in ('csv', 'xlsx'):
            rows = self.builder(request)
            fields = (('Faculty', 'faculty_names'), ('Employee Code', 'employee_code'), ('Department', 'department'),
                      ('Day', 'day'), ('Start Time', 'start_time'), ('End Time', 'end_time'),
                      ('Course Code', 'course_code'), ('Course Name', 'course_name'), ('Section', 'section'),
                      ('Program', 'program'), ('Year', 'year'), ('Semester', 'semester'), ('Academic Session', 'session'),
                      ('Room', 'room'), ('Delivery Mode', 'delivery_mode'), ('Block Length', 'block_length'))
            export_rows = [{label: row.get(key, '') for label, key in fields} for row in rows]
            return export_response(self.report_name, export_rows, fmt, [label for label, _ in fields])
        return super().get(request)


class FacultyTimetableVisualView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        invalid = _validate_faculty_timetable_filters(request)
        if invalid:
            return invalid
        if request.user.role == 'FACULTY':
            profile = getattr(request.user, 'faculty_profile', None)
            if not profile:
                return Response({'detail': 'No faculty profile is linked to this account.'}, status=403)
            if request.query_params.get('faculty') and str(request.query_params['faculty']) != str(profile.pk):
                return Response({'detail': 'You may only view your own timetable.'}, status=403)
        return Response(reporting.faculty_timetable_visual(request))


class FacultyTimetableOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'FACULTY':
            profile = getattr(request.user, 'faculty_profile', None)
            if not profile:
                return Response([])
            faculty_rows = Faculty.objects.filter(pk=profile.pk)
        else:
            faculty_rows = Faculty.objects.filter(active=True)
            if request.query_params.get('department'):
                faculty_rows = faculty_rows.filter(department_id=request.query_params['department'])
            if request.query_params.get('program'):
                faculty_rows = faculty_rows.filter(department__programs__pk=request.query_params['program'])
            version = reporting._version(request)
            if version:
                faculty_rows = faculty_rows.filter(department__institution=version.timetable.institution)
        faculty_rows = faculty_rows.select_related('user', 'department').distinct()
        return Response([{'id': str(item.pk), 'name': reporting.faculty_name(item),
                          'employee_code': item.employee_code or '', 'department': item.department.name}
                         for item in faculty_rows.order_by('name', 'employee_code', 'pk')])


class CourseAllocationView(ReportView):
    report_name = 'course-allocation'
    builder = staticmethod(reporting.course_allocation)

    def get(self, request):
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        invalid = _validate_course_allocation_filters(request)
        if invalid:
            return invalid
        fmt = (request.query_params.get('export') or request.query_params.get('format', '')).lower()
        if fmt in ('csv', 'xlsx'):
            rows = self.builder(request)
            fields = (('Course Code', 'course_code'), ('Course Name', 'course_name'), ('Section', 'section'),
                ('Program', 'program'), ('Year', 'year'), ('Semester', 'semester'), ('Academic Session', 'session'),
                ('Activity Type', 'activity_type_label'), ('Required Weekly Periods', 'required_periods'),
                ('Scheduled Periods', 'scheduled_periods'), ('Difference', 'difference'),
                ('Completion %', 'completion_percentage'), ('Status', 'status'), ('Faculty', 'faculty'),
                ('Preferred Room', 'preferred_room'), ('Required Room Type', 'required_room_type'),
                ('Actual Rooms', 'actual_rooms'), ('Delivery Mode', 'delivery_modes'))
            export_rows = [{label: ', '.join(row[key]) if isinstance(row.get(key), list) else row.get(key, '')
                            for label, key in fields} for row in rows]
            return export_response(self.report_name, export_rows, fmt, [label for label, _ in fields])
        return super().get(request)


class CourseAllocationVisualView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        invalid = _validate_course_allocation_filters(request)
        if invalid:
            return invalid
        return Response(reporting.course_allocation_visual(request))


class CourseAllocationOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        return Response(reporting.course_allocation_options(request))


def _validate_course_allocation_filters(request):
    for key in ('semester', 'session', 'academic_session', 'program', 'section', 'course', 'faculty'):
        value = request.query_params.get(key)
        if value:
            try:
                UUID(str(value))
            except (TypeError, ValueError):
                return Response({'detail': f'{key} must be a valid ID.'}, status=400)
    if request.query_params.get('allocation_status') and request.query_params['allocation_status'].upper() not in {
        'COMPLETE', 'UNDER_SCHEDULED', 'UNSCHEDULED', 'OVER_SCHEDULED'
    }:
        return Response({'detail': 'allocation_status is not a supported allocation status.'}, status=400)
    if request.query_params.get('activity_type') and request.query_params['activity_type'] not in dict(reporting.CourseOffering.ClassType.choices):
        return Response({'detail': 'activity_type is not a supported class type.'}, status=400)
    raw_year = request.query_params.get('year')
    if raw_year not in (None, ''):
        try:
            if int(raw_year) < 1:
                raise ValueError
        except (TypeError, ValueError):
            return Response({'detail': 'year must be a positive integer.'}, status=400)
    return None


def _validate_faculty_timetable_filters(request):
    for key in ('faculty', 'program', 'department', 'semester', 'session', 'academic_session', 'version', 'version_id'):
        value = request.query_params.get(key)
        if value:
            try:
                UUID(str(value))
            except (TypeError, ValueError):
                return Response({'detail': f'{key} must be a valid ID.'}, status=400)
    raw_weekday = request.query_params.get('weekday')
    if raw_weekday not in (None, ''):
        try:
            weekday = int(raw_weekday)
        except (TypeError, ValueError):
            return Response({'detail': 'weekday must be between 0 (Monday) and 4 (Friday).'}, status=400)
        if weekday not in range(5):
            return Response({'detail': 'weekday must be between 0 (Monday) and 4 (Friday).'}, status=400)
class RoomTimetableView(ReportView):
    report_name = 'room-timetable'
    builder = staticmethod(reporting.room_timetable)

    def get(self, request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        invalid = _validate_room_timetable_filters(request)
        if invalid:
            return invalid
        fmt = (request.query_params.get('export') or request.query_params.get('format', '')).lower()
        if fmt in ('csv', 'xlsx'):
            source = self.builder(request)
            fields = (('Room', 'room'), ('Day', 'day'), ('Start Time', 'start_time'), ('End Time', 'end_time'),
                      ('Course Code', 'course_code'), ('Course Name', 'course_name'), ('Section', 'section'),
                      ('Program', 'program'), ('Year', 'year'), ('Semester', 'semester'), ('Faculty', 'faculty_names'),
                      ('Delivery Mode', 'delivery_mode'))
            rows = [{label: row.get(key, '') for label, key in fields} for row in source]
            return export_response(self.report_name, rows, fmt, [label for label, _ in fields])
        return super().get(request)


class RoomTimetableVisualView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        invalid = _validate_room_timetable_filters(request)
        if invalid:
            return invalid
        return Response(reporting.room_timetable_visual(request))


class RoomTimetableOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        rooms = Room.objects.filter(active=True).order_by('code')
        return Response([{'id': str(room.pk), 'name': f'{room.code} — {room.get_room_type_display()}'} for room in rooms])


def _validate_room_timetable_filters(request):
    room_id = request.query_params.get('room')
    if room_id:
        try:
            UUID(str(room_id))
        except (TypeError, ValueError):
            return Response({'detail': 'room must be a valid room ID.'}, status=400)
        if not Room.objects.filter(pk=room_id, active=True).exists():
            return Response({'detail': 'The selected room does not exist or is inactive.'}, status=400)
    raw_weekday = request.query_params.get('weekday')
    if raw_weekday not in (None, ''):
        try:
            weekday = int(raw_weekday)
        except (TypeError, ValueError):
            return Response({'detail': 'weekday must be between 0 (Monday) and 4 (Friday).'}, status=400)
        if weekday not in range(5):
            return Response({'detail': 'weekday must be between 0 (Monday) and 4 (Friday).'}, status=400)
    raw_year = request.query_params.get('year')
    if raw_year not in (None, ''):
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            return Response({'detail': 'year must be a positive integer.'}, status=400)
        if year < 1:
            return Response({'detail': 'year must be a positive integer.'}, status=400)
    return None


def _validate_section_timetable_filters(request):
    for key in ('section', 'program', 'semester', 'session', 'academic_session'):
        value = request.query_params.get(key)
        if value:
            try:
                UUID(str(value))
            except (TypeError, ValueError):
                return Response({'detail': f'{key} must be a valid ID.'}, status=400)
    raw_year = request.query_params.get('year')
    if raw_year not in (None, ''):
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            return Response({'detail': 'year must be a positive integer.'}, status=400)
        if year < 1:
            return Response({'detail': 'year must be a positive integer.'}, status=400)
    raw_weekday = request.query_params.get('weekday')
    if raw_weekday not in (None, ''):
        try:
            weekday = int(raw_weekday)
        except (TypeError, ValueError):
            return Response({'detail': 'weekday must be between 0 (Monday) and 4 (Friday).'}, status=400)
        if weekday not in range(5):
            return Response({'detail': 'weekday must be between 0 (Monday) and 4 (Friday).'}, status=400)
    return None
class UnscheduledView(ReportView):
    report_name='unscheduled'
    builder=staticmethod(reporting.unscheduled)

    def get(self, request):
        if request.user.role == 'FACULTY':
            return Response({'detail': 'You do not have permission to access this report.'}, status=403)
        fmt = (request.query_params.get('export') or request.query_params.get('format', '')).lower()
        if fmt in ('csv', 'xlsx'):
            fields = (('Course Code', 'course_code'), ('Course Name', 'course_name'), ('Section', 'section'),
                ('Program', 'program'), ('Year', 'year'), ('Semester', 'semester'), ('Academic Session', 'session'),
                ('Required Periods', 'required_periods'), ('Scheduled Periods', 'scheduled_periods'),
                ('Remaining Periods', 'remaining_periods'), ('Difference', 'difference'), ('Status', 'status'),
                ('Faculty', 'faculty'), ('Activity Type', 'activity_type_label'),
                ('Required Room Type', 'required_room_type'), ('Preferred Room', 'preferred_room'))
            rows = [{label: ', '.join(row[key]) if isinstance(row.get(key), list) else
                     max(row['required_periods'] - row['scheduled_periods'], 0) if key == 'remaining_periods' else row.get(key, '')
                     for label, key in fields} for row in self.builder(request)]
            return export_response(self.report_name, rows, fmt, [label for label, _ in fields])
        return Response(reporting.unscheduled_analytics(request))
class ConflictsView(ReportView): report_name='conflicts'; builder=staticmethod(reporting.conflicts)
class VersionActivityView(ReportView): report_name='version-activity'; builder=staticmethod(reporting.version_activity)
class AnalyticsView(ReportView):
    report_name='analytics'; builder=staticmethod(reporting.analytics)
    @extend_schema(responses=inline_serializer(name='ReportAnalytics',fields={'total_faculty':serializers.IntegerField(),'total_rooms':serializers.IntegerField(),'total_sections':serializers.IntegerField(),'scheduled_classes':serializers.IntegerField(),'room_utilization_percentage':serializers.FloatField(),'average_faculty_workload':serializers.FloatField(),'under_scheduled_courses':serializers.IntegerField(),'weekday_load':serializers.DictField(),'time_slot_load':serializers.DictField()}))
    def get(self,request): return super().get(request)
class FreeRoomsView(ReportView):
    report_name='free-rooms'; builder=staticmethod(reporting.free_rooms)
    def get(self,request):
        invalid = _validate_schedule_scope(request)
        if invalid: return invalid
        raw=request.query_params.get('weekday')
        if raw is not None:
            try: weekday=int(raw)
            except (TypeError,ValueError): return Response({'detail':'weekday must be an integer from 0 (Monday) through 6 (Sunday).'},status=400)
            if weekday not in range(7): return Response({'detail':'weekday must be an integer from 0 (Monday) through 6 (Sunday).'},status=400)
        raw_capacity = request.query_params.get('capacity', '')
        if raw_capacity not in (None, ''):
            try:
                if int(raw_capacity) < 0: raise ValueError
            except (TypeError, ValueError):
                return Response({'detail': 'capacity must be a non-negative integer.'}, status=400)
        room_type = request.query_params.get('room_type')
        if room_type and room_type not in Room.RoomType.values:
            return Response({'detail': 'room_type must be a canonical room type value.'}, status=400)
        raw_slot = request.query_params.get('time_slot') or request.query_params.get('start_slot')
        if raw_slot:
            try:
                UUID(str(raw_slot))
            except (TypeError, ValueError):
                return Response({'detail': 'time_slot must be a valid ID.'}, status=400)
            if not TimeSlot.objects.filter(pk=raw_slot).exists():
                return Response({'detail': 'time_slot was not found.'}, status=400)
        result = self.builder(request)
        fmt = (request.query_params.get('export') or request.query_params.get('format', '')).lower()
        if fmt in ('csv', 'xlsx'):
            fields = (('Room', 'room_no'), ('Room Type', 'room_type'), ('Capacity', 'capacity'),
                      ('Building', 'building'), ('Floor', 'floor'), ('Projector', 'projector'))
            export_rows = [{label: row.get(key, '') for label, key in fields} for row in result['rooms']]
            return export_response(self.report_name, export_rows, fmt, [label for label, _ in fields])
        return Response(result)
class FacultyArrangementsView(ReportView): report_name='faculty-arrangements'; builder=staticmethod(reporting.faculty_arrangements)

class HealthView(APIView):
    permission_classes=[]
    authentication_classes=[]
    @extend_schema(responses=inline_serializer(name='HealthResponse', fields={'status': serializers.CharField()}))
    def get(self, request):
        return Response({'status':'ok'})
