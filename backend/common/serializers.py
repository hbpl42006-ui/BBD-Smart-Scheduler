from rest_framework import serializers
from accounts.models import User
from institutions.models import Institution, Department, Program
from academics.models import AcademicSession, Semester, Section, Course, CourseOffering
from faculty.models import Faculty, FacultyAvailability, CourseOfferingFaculty
from rooms.models import Room, RoomAvailability
from common.models import TimeSlotTemplate, TimeSlot

class FriendlyModelSerializer(serializers.ModelSerializer):
    class Meta:
        fields = '__all__'

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id','email','first_name','last_name','role','is_active','is_staff')

class InstitutionSerializer(FriendlyModelSerializer):
    class Meta: model = Institution; fields = '__all__'
class DepartmentSerializer(FriendlyModelSerializer):
    institution_name = serializers.CharField(source='institution.name', read_only=True)
    class Meta: model = Department; fields = '__all__'
class ProgramSerializer(FriendlyModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    class Meta: model = Program; fields = '__all__'
class AcademicSessionSerializer(FriendlyModelSerializer):
    class Meta: model = AcademicSession; fields = '__all__'
class SemesterSerializer(FriendlyModelSerializer):
    session_name = serializers.CharField(source='session.name', read_only=True)
    academic_session = serializers.UUIDField(source='session_id', read_only=True)
    class Meta: model = Semester; fields = '__all__'
class SectionSerializer(FriendlyModelSerializer):
    program_name = serializers.CharField(source='program.name', read_only=True)
    semester_name = serializers.CharField(source='semester.name', read_only=True)
    class Meta: model = Section; fields = '__all__'
class CourseSerializer(FriendlyModelSerializer):
    class Meta: model = Course; fields = '__all__'
class CourseOfferingSerializer(FriendlyModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    course_code = serializers.CharField(source='course.code', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)
    class Meta: model = CourseOffering; fields = '__all__'
class FacultySerializer(FriendlyModelSerializer):
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source='user.email', read_only=True)
    class Meta: model = Faculty; fields = '__all__'; extra_fields = ('name','email')
    def get_name(self,obj): return f'{obj.user.first_name} {obj.user.last_name}'.strip()
    @classmethod
    def _declared_fields(cls): return super()._declared_fields()
class FacultyAvailabilitySerializer(FriendlyModelSerializer):
    class Meta: model = FacultyAvailability; fields = '__all__'
class CourseOfferingFacultySerializer(FriendlyModelSerializer):
    class Meta: model = CourseOfferingFaculty; fields = '__all__'
class RoomSerializer(FriendlyModelSerializer):
    class Meta: model = Room; fields = '__all__'
class RoomAvailabilitySerializer(FriendlyModelSerializer):
    class Meta: model = RoomAvailability; fields = '__all__'
class TimeSlotTemplateSerializer(FriendlyModelSerializer):
    class Meta: model = TimeSlotTemplate; fields = '__all__'
class TimeSlotSerializer(FriendlyModelSerializer):
    class Meta: model = TimeSlot; fields = '__all__'
