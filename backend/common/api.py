from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
from openpyxl import Workbook
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from accounts.models import User
from institutions.models import Institution, Department, Program
from academics.models import AcademicSession, Semester, Section, Course, CourseOffering
from faculty.models import Faculty, FacultyAvailability, CourseOfferingFaculty
from rooms.models import Room, RoomAvailability
from common.models import TimeSlotTemplate, TimeSlot
from common.permissions import RolePermission, WRITE_ROLES
from common.serializers import *
from common.imports import rows_from_upload, validate, commit, SPECS
from common.import_services import faculty_import as faculty_import_rows, course_import as course_import_rows, mapping_import, section_import, availability_import
from common.course_offering_import import parse as parse_course_offerings, commit as commit_course_offerings, template as course_offering_template

class LoginSerializer(TokenObtainPairSerializer):
    username_field = 'email'
    def validate(self, attrs):
        data = super().validate(attrs); data['user'] = UserSerializer(self.user).data; return data
class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]; serializer_class = LoginSerializer

class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]
    filterset_fields = {'id': ['exact']}
    search_fields = ()
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
class SectionViewSet(BaseViewSet): queryset=Section.objects.select_related('program','semester','coordinator'); serializer_class=SectionSerializer; filterset_fields=('program','semester','year')
class CourseViewSet(BaseViewSet): queryset=Course.objects.all(); serializer_class=CourseSerializer; search_fields=('code','name','short_code')
class CourseOfferingViewSet(BaseViewSet): queryset=CourseOffering.objects.select_related('course','section','semester','preferred_room'); serializer_class=CourseOfferingSerializer; filterset_fields=('semester','section','course','active')
class FacultyViewSet(BaseViewSet):
    queryset=Faculty.objects.select_related('user','department')
    serializer_class=FacultySerializer
    filterset_fields=('department',)
    search_fields=('user__first_name','user__last_name','user__email','employee_code','initials')
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
class RoomViewSet(BaseViewSet): queryset=Room.objects.all(); serializer_class=RoomSerializer; filterset_fields=('building','floor','room_type','active'); search_fields=('code','building')
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
    return Response({'active_session': {'id':str(session.id),'name':session.name} if session else None,
        'active_semester': {'id':str(semester.id),'name':semester.name} if semester else None,
        'counts': {'programs':Program.objects.count(),'sections':Section.objects.count(),'faculty':Faculty.objects.count(),'rooms':Room.objects.count(),'courses':Course.objects.count(),'course_offerings':CourseOffering.objects.filter(active=True).count()},
        'readiness': {'academic_session':bool(session),'sections':Section.objects.exists(),'courses':Course.objects.exists(),'faculty':Faculty.objects.exists(),'faculty_availability':FacultyAvailability.objects.exists(),'rooms':Room.objects.exists(),'time_slots':TimeSlot.objects.exists(),'course_offerings':CourseOffering.objects.filter(active=True).exists()}})

@api_view(['POST'])
@permission_classes([RolePermission])
def import_data(request, action, kind=None):
    kind = kind or request.path.strip('/').split('/')[2]
    if kind not in SPECS or action not in ('preview','commit'): return Response({'detail':'Unsupported import'},status=404)
    upload=request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
    try: rows=rows_from_upload(upload); valid,errors=validate(kind,rows)
    except Exception as exc: return Response({'detail':str(exc)},status=400)
    if kind=='faculty':
        try: return Response(faculty_import_rows(rows,commit=action=='commit'), status=201 if action=='commit' else 200)
        except Exception: return Response({'detail':'The uploaded Faculty file could not be processed.'},status=400)
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
        return Response({'valid':not errors,'total_rows':len(rows),'valid_rows':len(valid)-skipped,'invalid_rows':len(errors),'skipped':skipped,'errors':errors,'rows':valid})
    if errors: return Response({'valid':False,'errors':errors},status=400)
    try: result=commit(kind,rows)
    except ValueError as exc: return Response({'valid':False,'errors':exc.args[0]},status=400)
    return Response(result,status=201)

@api_view(['GET'])
@permission_classes([RolePermission])
def room_import_template(request):
    if not (request.user.is_superuser or request.user.role in WRITE_ROLES):
        return Response({'detail':'You do not have permission to import rooms.'}, status=403)
    workbook = Workbook(); sheet = workbook.active; sheet.title = 'Rooms'
    sheet.append(['Room No.', 'Building', 'Floor', 'Capacity', 'Room Type', 'Active'])
    sheet.append(['401', 'Main', '4', 60, 'Classroom', 'true'])
    sheet.append(['402', 'Main', '4', 60, 'Classroom', 'true'])
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
