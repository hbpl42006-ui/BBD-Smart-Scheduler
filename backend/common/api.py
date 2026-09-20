from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
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
    if action=='preview': return Response({'valid':not errors,'total_rows':len(rows),'valid_rows':len(valid),'invalid_rows':len(errors),'errors':errors,'rows':valid})
    if errors: return Response({'valid':False,'errors':errors},status=400)
    try: result=commit(kind,rows)
    except ValueError as exc: return Response({'valid':False,'errors':exc.args[0]},status=400)
    return Response(result,status=201)
