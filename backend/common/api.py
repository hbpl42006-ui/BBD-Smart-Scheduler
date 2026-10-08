from django.db.models import Q
from django.db.models.deletion import ProtectedError
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import BaseRenderer
import logging
from openpyxl import Workbook
from academics.delivery import get_section_delivery_policy
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from accounts.models import User
from institutions.models import Institution, Department, Program
from academics.models import AcademicSession, Semester, Section, Course, CourseOffering
from faculty.models import Faculty, FacultyAvailability, CourseOfferingFaculty, FacultyArrangement
from scheduling.models import ScheduleEntryFaculty
from rooms.models import Room, RoomAvailability
from common.models import TimeSlotTemplate, TimeSlot
from common.permissions import RolePermission, WRITE_ROLES
from common.serializers import *
from common.imports import rows_from_upload, validate, commit, SPECS
from common.import_services import faculty_import_safe as faculty_import_rows, course_import as course_import_rows, mapping_import, section_import, availability_import, FacultyImportCommitError
from common.course_offering_import import parse as parse_course_offerings, commit as commit_course_offerings, template as course_offering_template
from common.export_utils import export_response


class ExportFileRenderer(BaseRenderer):
    """Negotiation marker: export actions return their own file HttpResponse."""
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class XLSXExportRenderer(ExportFileRenderer):
    media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    format = 'xlsx'


class CSVExportRenderer(ExportFileRenderer):
    media_type = 'text/csv'
    format = 'csv'

class LoginSerializer(TokenObtainPairSerializer):
    username_field = 'email'
    def validate(self, attrs):
        data = super().validate(attrs); data['user'] = UserSerializer(self.user).data; return data
class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]; serializer_class = LoginSerializer

class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]
    filter_backends = [*viewsets.ModelViewSet.filter_backends, SearchFilter]
    filterset_fields = {'id': ['exact']}
    search_fields = ()
    export_columns = ()
    export_title = ''
    export_sheet = ''
    @action(detail=False, methods=['get'], url_path='export', renderer_classes=[*viewsets.ModelViewSet.renderer_classes, XLSXExportRenderer, CSVExportRenderer])
    def export(self, request):
        if not self.export_columns:
            return Response({'detail':'Export is not available for this resource.'}, status=404)
        file_format = request.query_params.get('format', 'xlsx').lower()
        if file_format not in {'xlsx', 'csv'}:
            raise ValidationError({'format':'Choose xlsx or csv.'})
        queryset = self.filter_queryset(self.get_queryset())
        return export_response(queryset, file_format, self.export_sheet, self.export_columns, self.export_title)
    def perform_destroy(self, instance):
        if hasattr(instance, 'active'):
            instance.active = False; instance.save(update_fields=['active'])
        elif hasattr(instance, 'is_active'):
            instance.is_active = False; instance.save(update_fields=['is_active'])
        else: instance.delete()

class InstitutionViewSet(BaseViewSet): queryset=Institution.objects.all(); serializer_class=InstitutionSerializer; search_fields=('name','code')
class DepartmentViewSet(BaseViewSet): queryset=Department.objects.select_related('institution'); serializer_class=DepartmentSerializer; filterset_fields=('institution',); search_fields=('name','code')
class ProgramViewSet(BaseViewSet): queryset=Program.objects.select_related('department'); serializer_class=ProgramSerializer; filterset_fields=('department',); search_fields=('name','code')
class AcademicSessionViewSet(BaseViewSet): queryset=AcademicSession.objects.select_related('institution'); serializer_class=AcademicSessionSerializer; filterset_fields=('institution','is_active')
class SemesterViewSet(BaseViewSet): queryset=Semester.objects.select_related('session'); serializer_class=SemesterSerializer; filterset_fields=('session','type')
class SectionViewSet(BaseViewSet):
    queryset=Section.objects.select_related('program__department','semester__session','coordinator'); serializer_class=SectionSerializer; filterset_fields=('program','semester','year'); search_fields=('name','program__name','program__code')
    export_title='Sections'; export_sheet='Sections'
    export_columns=(('Session',lambda x:x.semester.session.name),('Semester',lambda x:x.semester.name),('Program',lambda x:x.program.name),('Academic Level / Year',lambda x:f'Year {x.year}'),('Section',lambda x:x.name),('Student Strength',lambda x:x.student_strength),('Class Coordinator',lambda x:(f'{x.coordinator.first_name} {x.coordinator.last_name}'.strip() or x.coordinator.email) if x.coordinator_id else ''),('Branch',lambda x:f'{x.program.code} - {x.program.department.name}'))
    def get_queryset(self):
        queryset=super().get_queryset(); session=self.request.query_params.get('session')
        return queryset.filter(semester__session_id=session) if session else queryset
class CourseViewSet(BaseViewSet):
    queryset=Course.objects.all(); serializer_class=CourseSerializer; search_fields=('code','name','short_code')
    export_title='Courses'; export_sheet='Courses'
    export_columns=(('Course Code',lambda x:x.code),('Course Name',lambda x:x.name),('Short Code',lambda x:x.short_code),('Credits',lambda x:x.credit),('Active',lambda x:'Yes' if x.active else 'No'))
    def get_queryset(self):
        queryset=super().get_queryset()
        status_filter = self.request.query_params.get('status', 'active').lower()
        include_inactive = self.request.query_params.get('include_inactive', '').lower() in ('1','true','yes')
        if status_filter == 'archived':
            queryset = queryset.filter(active=False)
        elif status_filter != 'all' and not include_inactive:
            queryset = queryset.filter(active=True)
        return queryset
    def destroy(self, request, *args, **kwargs):
        instance=self.get_object()
        if CourseOffering.objects.filter(course=instance).exists():
            return Response({'detail':'This course is referenced by course offerings and cannot be deleted. Archive it instead.','can_archive':True}, status=status.HTTP_409_CONFLICT)
        try:
            self.perform_destroy(instance)
        except ProtectedError:
            return Response({'detail':'This course is referenced by course offerings and cannot be deleted. Archive it instead.','can_archive':True}, status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        course = Course.objects.get(pk=pk); course.active = True; course.save(update_fields=['active']); return Response(self.get_serializer(course).data)
class CourseOfferingViewSet(BaseViewSet):
    queryset=CourseOffering.objects.select_related('course','section__program','semester__session','preferred_room').prefetch_related('faculties__faculty__department'); serializer_class=CourseOfferingSerializer; filterset_fields=('semester','section','course','active','default_class_type')
    search_fields=('course__code','course__name','section__name','faculties__faculty__employee_code','faculties__faculty__name')
    export_title='Course_Offerings'; export_sheet='Course Offerings'
    export_columns=(('Session',lambda x:x.semester.session.name),('Semester',lambda x:x.semester.name),('Course Code',lambda x:x.course.code),('Course Name',lambda x:x.course.name),('Section',lambda x:x.section.name),('Activity Type',lambda x:x.get_default_class_type_display()),('Weekly Periods',lambda x:x.weekly_periods),('Block Size',lambda x:x.required_block_size),('Allow Remainder Period',lambda x:'Yes' if x.allow_remainder_period else 'No'),('Faculty Employee Code',lambda x:', '.join(sorted((a.faculty.employee_code or '') for a in x.faculties.all() if a.faculty.employee_code))),('Faculty Name',lambda x:', '.join(sorted((a.faculty.name or a.faculty.initials or a.faculty.employee_code or '') for a in x.faculties.all()))),('Faculty Roles',lambda x:', '.join(sorted(a.role for a in x.faculties.all()))),('Preferred Room',lambda x:x.preferred_room.code if x.preferred_room_id else ''),('Room Type Requirement',lambda x:x.room_type_requirement),('Delivery Policy',lambda x:get_section_delivery_policy(x.section,x.semester)[0]),('Active',lambda x:'Yes' if x.active else 'No'))
    def get_queryset(self):
        queryset=super().get_queryset(); session=self.request.query_params.get('session'); faculty=self.request.query_params.get('faculty')
        if session: queryset=queryset.filter(semester__session_id=session)
        if faculty: queryset=queryset.filter(faculties__faculty_id=faculty).distinct()
        return queryset
class FacultyViewSet(BaseViewSet):
    queryset=Faculty.objects.select_related('user','department')
    serializer_class=FacultySerializer
    filterset_fields=('department',)
    search_fields=('user__first_name','user__last_name','user__email','employee_code','initials')
    export_title='Faculty'; export_sheet='Faculty'
    export_columns=(('Faculty Name',lambda x:x.name or (f'{x.user.first_name} {x.user.last_name}'.strip() if x.user_id else '') or x.initials or x.employee_code or ''),('Employee Code',lambda x:x.employee_code or ''),('Initials',lambda x:x.initials),('Department',lambda x:x.department.name),('Email',lambda x:x.email or (x.user.email if x.user_id else '')),('Login/User Linked',lambda x:'Yes' if x.user_id else 'No'),('Max Daily Periods',lambda x:x.max_daily_periods),('Max Weekly Periods',lambda x:x.max_weekly_periods),('Active',lambda x:'Yes' if x.active else 'No'))
    def get_queryset(self):
        queryset=super().get_queryset()
        status_filter = self.request.query_params.get('status', 'active').lower()
        include_inactive = self.request.query_params.get('include_inactive', '').lower() in ('1','true','yes')
        if status_filter == 'archived': queryset = queryset.filter(active=False)
        elif status_filter != 'all' and not include_inactive: queryset = queryset.filter(active=True)
        return queryset
    def destroy(self, request, *args, **kwargs):
        instance=self.get_object()
        referenced=(CourseOfferingFaculty.objects.filter(faculty=instance).exists() or ScheduleEntryFaculty.objects.filter(faculty=instance).exists() or FacultyArrangement.objects.filter(absent_faculty=instance).exists() or FacultyArrangement.objects.filter(substitute_faculty=instance).exists() or FacultyAvailability.objects.filter(faculty=instance).exists())
        if referenced:
            return Response({'detail':'This faculty member is used by academic or timetable records and cannot be permanently deleted.','can_archive':True}, status=status.HTTP_409_CONFLICT)
        # A Faculty profile is an optional profile for a User account.  Do not
        # cascade-delete the login when an otherwise-unused profile is removed.
        # The model's historical CASCADE is retained for other code paths, so
        # explicitly detach the account before deleting the profile.
        if instance.user_id:
            instance.user = None
            instance.save(update_fields=['user', 'updated_at'])
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        faculty=self.get_object(); faculty.active=False; faculty.save(update_fields=['active','updated_at']); return Response(self.get_serializer(faculty).data)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        faculty=Faculty.objects.get(pk=pk); faculty.active=True; faculty.save(update_fields=['active','updated_at']); return Response(self.get_serializer(faculty).data)
    @action(detail=True, methods=['get','post'], url_path='availability')
    def availability(self, request, pk=None):
        faculty = self.get_object()
        if request.method == 'GET':
            return Response(FacultyAvailabilitySerializer(faculty.availabilities.all(), many=True).data)
        if request.user.role not in WRITE_ROLES and not request.user.is_superuser: return Response({'detail':'You do not have permission to perform this action.'}, status=403)
        data = request.data.copy(); data['faculty'] = str(faculty.pk)
        serializer = FacultyAvailabilitySerializer(data=data)
        serializer.is_valid(raise_exception=True); serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
class FacultyAvailabilityViewSet(BaseViewSet): queryset=FacultyAvailability.objects.select_related('faculty','time_slot'); serializer_class=FacultyAvailabilitySerializer; filterset_fields=('faculty','weekday','is_available')
class RoomViewSet(BaseViewSet):
    queryset=Room.objects.select_related('reserved_program'); serializer_class=RoomSerializer; filterset_fields=('building','floor','room_type','active','has_projector','allowed_year'); search_fields=('code','building')
    export_title='Rooms_and_Labs'; export_sheet='Rooms & Labs'
    export_columns=(('Room No.',lambda x:x.code),('Building',lambda x:x.building),('Floor',lambda x:x.floor),('Capacity',lambda x:x.capacity),('Room Type',lambda x:x.get_room_type_display()),('Projector',lambda x:'Yes' if x.has_projector else 'No'),('Allowed Year',lambda x:f'Year {x.allowed_year} Only' if x.allowed_year else 'All Years'),('Exclusive Program',lambda x:x.reserved_program.name if x.reserved_program_id else ''),('Exclusive Year',lambda x:f'Year {x.reserved_year}' if x.reserved_year else ''),('Active',lambda x:'Yes' if x.active else 'No'))
    def get_queryset(self):
        queryset=super().get_queryset()
        section_id=self.request.query_params.get('section')
        course_offering_id=self.request.query_params.get('course_offering')
        from rooms.services.eligibility import required_course_room_code, required_mtech_room_code, room_section_error
        from academics.models import Section
        section=None;offering=None
        if section_id:
            from django.db.models import Q
            section=Section.objects.select_related('program').filter(pk=section_id).first()
            if not section:return queryset.none()
            required_code=required_mtech_room_code(section)
            offering=None
            if course_offering_id:
                from academics.models import CourseOffering
                offering=CourseOffering.objects.select_related('course').filter(pk=course_offering_id,section=section).first()
                required_code=required_course_room_code(offering) if offering else required_code
            allowed_year=Q(allowed_year__isnull=True)|Q(allowed_year=section.year)
            reservation_allowed=Q(exclusive_reservation=False)|Q(reserved_program_id=section.program_id,reserved_year=section.year)
            if offering:
                exception=Q(eligibility_exceptions__active=True,eligibility_exceptions__allow=True,eligibility_exceptions__program_id=section.program_id,eligibility_exceptions__year=section.year,eligibility_exceptions__course=offering.course)
                allowed_year|=exception
                reservation_allowed|=exception
            queryset=queryset.filter(active=True).filter(allowed_year)
            if required_code:
                queryset=queryset.filter(code=required_code)
            else:
                queryset=queryset.filter(reservation_allowed)
            queryset=queryset.distinct()
        year=self.request.query_params.get('year')
        if year not in (None,'') and not section_id:
            try: year=int(year)
            except ValueError: return queryset.none()
            queryset=queryset.filter(Q(allowed_year__isnull=True)|Q(allowed_year=year))
        queryset=queryset.select_related('reserved_program')
        if section:
            queryset=queryset.filter(pk__in=[room.pk for room in queryset if not room_section_error(room,section,offering.course if offering else None,required_course_room_code(offering) if offering else None)])
        return queryset
class RoomAvailabilityViewSet(BaseViewSet): queryset=RoomAvailability.objects.select_related('room','time_slot'); serializer_class=RoomAvailabilitySerializer; filterset_fields=('room','weekday','status')
class TimeSlotTemplateViewSet(BaseViewSet): queryset=TimeSlotTemplate.objects.select_related('institution'); serializer_class=TimeSlotTemplateSerializer; filterset_fields=('institution',)
class TimeSlotViewSet(BaseViewSet): queryset=TimeSlot.objects.select_related('template'); serializer_class=TimeSlotSerializer; filterset_fields=('template','is_break')

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request): return Response(UserSerializer(request.user).data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    session = AcademicSession.objects.filter(is_active=True).order_by('-start_date').first()
    semester = Semester.objects.filter(session=session).order_by('-number').first() if session else None
    availability_restrictions = FacultyAvailability.objects.filter(is_available=False).count()
    availability_configured = FacultyAvailability.objects.exists()
    return Response({'active_session': {'id':str(session.id),'name':session.name} if session else None,
        'active_semester': {'id':str(semester.id),'name':semester.name} if semester else None,
        'counts': {'programs':Program.objects.count(),'sections':Section.objects.count(),'faculty':Faculty.objects.count(),'rooms':Room.objects.count(),'courses':Course.objects.count(),'course_offerings':CourseOffering.objects.filter(active=True).count()},
        'readiness': {'academic_session':bool(session),'sections':Section.objects.exists(),'courses':Course.objects.exists(),'faculty':Faculty.objects.exists(),'faculty_availability':True,'rooms':Room.objects.exists(),'time_slots':TimeSlot.objects.exists(),'course_offerings':CourseOffering.objects.filter(active=True).exists()},
        'faculty_availability_status': 'CONFIGURED' if availability_configured else 'DEFAULT',
        'faculty_availability_restrictions': availability_restrictions})

@api_view(['POST'])
@permission_classes([RolePermission])
def import_data(request, action, kind=None):
    kind = kind or request.path.strip('/').split('/')[2]
    if kind not in SPECS or action not in ('preview','commit'): return Response({'detail':'Unsupported import'},status=404)
    upload=request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
    mode=str(request.data.get('import_mode','UPSERT')).strip().upper() if kind in ('courses','faculty') else 'CREATE_ONLY'
    if mode not in ('CREATE_ONLY','UPSERT','REPLACE'): return Response({'detail':'Invalid course import mode.'},status=400)
    try:
        rows=rows_from_upload(upload)
        valid,errors=(rows,[]) if kind=='faculty' else validate(kind,rows,mode)
    except Exception as exc: return Response({'detail':str(exc)},status=400)
    if kind=='faculty':
        try:
            result=faculty_import_rows(rows,commit=action=='commit',mode=mode)
            result.update({'total_rows':len(rows),'valid_rows':len(rows)-result['failed'],'invalid_rows':result['failed'],'create':result['created'],'update':result.get('updated',0),'skip':result.get('skipped',0),'archive':result.get('archive',0)})
            return Response(result, status=201 if action=='commit' else 200)
        except FacultyImportCommitError as exc:
            logging.getLogger(__name__).exception('Faculty import commit failed at row=%s faculty=%s employee_code=%s', exc.row, exc.faculty_name, exc.employee_code)
            return Response({'detail':'Faculty import failed.','row':exc.row,'faculty_name':exc.faculty_name,'employee_code':exc.employee_code,'error_type':exc.cause.__class__.__name__,'error':str(exc.cause)}, status=400)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)
        except Exception as exc:
            logging.getLogger(__name__).exception('UNEXPECTED Faculty import commit failure')
            raise
    if kind=='sections':
        try:
            result=section_import(rows,commit=action=='commit'); result.update({'total_rows':len(rows),'valid_rows':result['created'],'invalid_rows':result['failed']}); return Response(result,status=201 if action=='commit' else 200)
        except Exception: return Response({'detail':'The uploaded Section file could not be processed.'},status=400)
    if kind=='faculty-availability':
        try:
            result=availability_import(rows,commit=action=='commit'); result.update({'total_rows':len(rows),'valid_rows':len(rows)-result['failed'],'invalid_rows':result['failed']}); return Response(result,status=201 if action=='commit' else 200)
        except Exception: return Response({'detail':'The uploaded Faculty Availability file could not be processed.'},status=400)
    if action=='preview':
        skipped=sum(1 for row in valid if row.get('_skip'))
        updates=sum(1 for row in valid if row.get('_update_id')); creates=sum(1 for row in valid if not row.get('_update_id') and not row.get('_skip'))
        archive=Course.objects.filter(active=True).exclude(code__in={row['code'] for row in valid}).count() if kind=='courses' and mode=='REPLACE' and not errors else 0
        return Response({'valid':not errors,'total_rows':len(rows),'valid_rows':len(valid)-skipped,'invalid_rows':len(errors),'skipped':skipped,'create':creates,'update':updates,'archive':archive,'warnings':0,'errors':errors,'rows':valid})
    if errors: return Response({'valid':False,'errors':errors},status=400)
    try: result=commit(kind,rows,mode)
    except ValueError as exc: return Response({'valid':False,'errors':exc.args[0]},status=400)
    return Response(result,status=201)

@api_view(['GET'])
@permission_classes([RolePermission])
def room_import_template(request):
    if not (request.user.is_superuser or request.user.role in WRITE_ROLES):
        return Response({'detail':'You do not have permission to import rooms.'}, status=403)
    workbook = Workbook(); sheet = workbook.active; sheet.title = 'Rooms'
    sheet.append(['Room No.', 'Building', 'Floor', 'Capacity', 'Room Type', 'Projector', 'Active', 'Allowed Year'])
    sheet.append(['401', 'Main', '4', 60, 'Classroom', 'Yes', 'true', 'ALL'])
    sheet.append(['402', 'Main', '4', 60, 'Classroom', 'No', 'true', 'ALL'])
    from io import BytesIO
    stream = BytesIO(); workbook.save(stream)
    return HttpResponse(stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition':'attachment; filename="rooms-import-template.xlsx"'})

@api_view(['POST'])
@permission_classes([RolePermission])
def course_offering_import(request, action):
    upload=request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
    try: result, prepared = parse_course_offerings(rows_from_upload(upload))
    except Exception: return Response({'detail':'The uploaded Course Offerings file could not be read.'},status=400)
    if action=='preview': return Response(result)
    if result['invalid']: return Response(result,status=400)
    return Response(commit_course_offerings(prepared),status=201)

@api_view(['GET'])
@permission_classes([RolePermission])
def course_offering_import_template(request):
    return HttpResponse(course_offering_template(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="course-offerings-import-template.xlsx"'})

def _xlsx_template(filename, headers, example):
    workbook=Workbook(); sheet=workbook.active; sheet.append(headers); sheet.append(example)
    from io import BytesIO
    stream=BytesIO(); workbook.save(stream)
    return HttpResponse(stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{filename}"'})

@api_view(['POST','GET'])
@permission_classes([RolePermission])
def faculty_import(request):
    if request.method=='GET': return _xlsx_template('faculty-import-template.xlsx',['Employee Code','Faculty Name','Department','Email','Max Weekly Load','Active'],['FAC001','Aarav Sharma','Computer Science','',16,'true'])
    upload=request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
    try: return Response(faculty_import_rows(rows_from_upload(upload)),status=201)
    except Exception: return Response({'detail':'The uploaded file could not be read.'},status=400)

@api_view(['POST','GET'])
@permission_classes([RolePermission])
def course_import(request):
    if request.method=='GET': return _xlsx_template('course-import-template.xlsx',['Course Code','Course Name','Program','Semester','Lecture Hours','Tutorial Hours','Practical Hours'],['CS101','Programming Fundamentals','BTECH-CSE','1',3,1,0])
    upload=request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
    try: return Response(course_import_rows(rows_from_upload(upload)),status=201)
    except Exception: return Response({'detail':'The uploaded file could not be read.'},status=400)

@api_view(['POST','GET'])
@permission_classes([RolePermission])
def faculty_course_mapping_import(request):
    if request.method=='GET': return _xlsx_template('faculty-course-mapping-template.xlsx',['Employee Code','Course Code','Section','Session','Semester','Faculty Role'],['FAC001','CS101','CS 1A','2026-27','1','PRIMARY'])
    upload=request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
    try: return Response(mapping_import(rows_from_upload(upload)),status=201)
    except Exception: return Response({'detail':'The uploaded file could not be read.'},status=400)

@api_view(['GET'])
@permission_classes([RolePermission])
def faculty_availability_template(request):
    return _xlsx_template('faculty-availability-template.xlsx',['Employee Code','Weekday','Time Slot','Available','Preference Weight'],['FAC001','Monday','09:00-10:00','Yes',0])
