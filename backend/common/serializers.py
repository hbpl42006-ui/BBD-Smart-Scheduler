from rest_framework import serializers
from django.db import transaction
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
    faculty_id = serializers.UUIDField(source='faculty_profile.id', read_only=True, allow_null=True)
    class Meta:
        model = User
        fields = ('id','email','first_name','last_name','role','is_active','is_staff','faculty_id')

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
    email = serializers.EmailField(required=False, allow_blank=True, write_only=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    has_login = serializers.SerializerMethodField()
    class Meta: model = Faculty; fields = ('id','user','employee_code','initials','department','max_daily_periods','max_weekly_periods','name','email','password','has_login','created_at','updated_at'); read_only_fields=('user','created_at','updated_at','has_login')
    def get_name(self,obj):
        if obj.name: return obj.name
        if obj.user:
            user_name = f'{obj.user.first_name} {obj.user.last_name}'.strip()
            if user_name: return user_name
        return obj.initials or obj.employee_code or 'Unnamed faculty'
    def get_has_login(self,obj): return bool(obj.user_id)
    def validate_email(self,value):
        if value and User.objects.filter(email=value).exclude(pk=self.instance.user_id if self.instance and self.instance.user_id else None).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value
    def to_representation(self,obj):
        data=super().to_representation(obj)
        data['name']=self.get_name(obj)
        data['email']=obj.user.email if obj.user else None
        return data
    def _sync_user_name(self,user,name):
        parts=name.strip().split();user.first_name=parts[0] if parts else '';user.last_name=' '.join(parts[1:]);
    def _account(self,validated,instance=None):
        email=validated.pop('email',None);password=validated.pop('password',None);name=validated.get('name',instance.name if instance else '')
        user=instance.user if instance else None
        if not user and (email or password):
            if not email: raise serializers.ValidationError({'email':'Email is required to create a faculty login account.'})
            if not password: raise serializers.ValidationError({'password':'Password is required to create a faculty login account.'})
            user=User(email=email,role='FACULTY');user.set_password(password);self._sync_user_name(user,name);user.save()
        elif user:
            if email: user.email=email
            if password: user.set_password(password)
            if name: self._sync_user_name(user,name)
            user.save()
        return user
    def create(self,validated):
        with transaction.atomic():
            user=self._account(validated);return Faculty.objects.create(user=user,**validated)
    def update(self,instance,validated):
        with transaction.atomic():
            user=self._account(validated,instance);instance.user=user;return super().update(instance,validated)
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
