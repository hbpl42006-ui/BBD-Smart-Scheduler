from rest_framework import serializers
from django.db import transaction
from accounts.models import User
from institutions.models import Institution, Department, Program
from academics.models import AcademicSession, Semester, Section, Course, CourseOffering
from faculty.models import Faculty, FacultyAvailability, CourseOfferingFaculty
from rooms.models import Room, RoomAvailability
from common.models import TimeSlotTemplate, TimeSlot
from faculty.email_service import sync_faculty_email
from scheduling.weekdays import WORKING_DAYS

class FriendlyModelSerializer(serializers.ModelSerializer):
    class Meta:
        fields = '__all__'

class UserSerializer(serializers.ModelSerializer):
    faculty_id = serializers.UUIDField(source='faculty_profile.id', read_only=True, allow_null=True)
    display_name = serializers.SerializerMethodField()
    faculty = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ('id','email','first_name','last_name','role','is_active','is_staff','faculty_id','display_name','faculty')
    def get_display_name(self,obj):
        faculty = getattr(obj, 'faculty_profile', None)
        return (faculty.name if faculty and faculty.name else f'{obj.first_name} {obj.last_name}'.strip() or obj.email or 'User')
    def get_faculty(self,obj):
        faculty = getattr(obj, 'faculty_profile', None)
        if not faculty: return None
        return {'id': str(faculty.pk), 'name': faculty.name, 'employee_code': faculty.employee_code, 'initials': faculty.initials}

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
    effective_delivery_policy = serializers.SerializerMethodField()
    effective_offline_weekday = serializers.SerializerMethodField()
    class Meta:
        model = Section
        fields = ('id','program','semester','year','name','student_strength','delivery_policy','offline_weekday','effective_delivery_policy','effective_offline_weekday','coordinator','created_at','updated_at','program_name','semester_name')
    def _effective_policy(self, obj):
        from academics.delivery import get_section_delivery_policy
        return get_section_delivery_policy(obj, obj.semester)
    def get_effective_delivery_policy(self, obj):
        return self._effective_policy(obj)[0]
    def get_effective_offline_weekday(self, obj):
        return self._effective_policy(obj)[1]
    def validate(self, attrs):
        policy = attrs.get('delivery_policy', self.instance.delivery_policy if self.instance else Section.DeliveryPolicy.STANDARD)
        offline_day = attrs.get('offline_weekday', self.instance.offline_weekday if self.instance else None)
        if policy == Section.DeliveryPolicy.HYBRID and offline_day is None:
            raise serializers.ValidationError({'offline_weekday': 'A hybrid section must have an offline weekday configured.'})
        if policy == Section.DeliveryPolicy.HYBRID and offline_day not in WORKING_DAYS:
            raise serializers.ValidationError({'offline_weekday': 'Hybrid offline day must be a working day (Monday-Friday).'})
        if policy != Section.DeliveryPolicy.HYBRID and offline_day is not None:
            raise serializers.ValidationError({'offline_weekday': 'Offline weekday is only valid for hybrid sections.'})
        return attrs
class CourseSerializer(FriendlyModelSerializer):
    class Meta: model = Course; fields = '__all__'
class CourseOfferingSerializer(FriendlyModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    course_code = serializers.CharField(source='course.code', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)
    faculty_assignments = serializers.SerializerMethodField()
    class Meta: model = CourseOffering; fields = '__all__'
    def get_faculty_assignments(self,obj):
        return [{'faculty_id':str(item.faculty_id),'role':item.role,'priority':item.priority,'name':item.faculty.name,'employee_code':item.faculty.employee_code,'initials':item.faculty.initials} for item in obj.faculties.all()]
class FacultySerializer(FriendlyModelSerializer):
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    create_login = serializers.BooleanField(required=False, default=False, write_only=True)
    has_login = serializers.SerializerMethodField()
    class Meta: model = Faculty; fields = ('id','user','employee_code','initials','department','max_daily_periods','max_weekly_periods','active','name','email','password','create_login','has_login','created_at','updated_at'); read_only_fields=('user','created_at','updated_at','has_login')
    def validate(self, attrs):
        active=attrs.get('active', self.instance.active if self.instance else True)
        email=attrs.get('email', self.instance.email if self.instance else None)
        if active and not str(email or '').strip(): raise serializers.ValidationError({'email':'Email is required for active faculty.'})
        return attrs
    def get_name(self,obj):
        if obj.name: return obj.name
        if obj.user:
            user_name = f'{obj.user.first_name} {obj.user.last_name}'.strip()
            if user_name: return user_name
        return obj.initials or obj.employee_code or 'Unnamed faculty'
    def get_has_login(self,obj): return bool(obj.user_id)
    def validate_email(self,value):
        if value and self.instance and self.instance.user_id and User.objects.filter(email__iexact=value).exclude(pk=self.instance.user_id).exists():
            raise serializers.ValidationError('This email is already used by another user account.')
        return value
    def to_representation(self,obj):
        data=super().to_representation(obj)
        data['name']=self.get_name(obj)
        data['email']=obj.email
        return data
    def _sync_user_name(self,user,name):
        parts=name.strip().split();user.first_name=parts[0] if parts else '';user.last_name=' '.join(parts[1:]);
    def _account(self,validated,instance=None):
        email=validated.pop('email',None);password=validated.pop('password',None);create_login=validated.pop('create_login',False);name=validated.get('name',instance.name if instance else '')
        user=instance.user if instance else None
        if create_login and not user:
            if not email: raise serializers.ValidationError({'email':'Email is required to create a faculty login account.'})
            if not password: raise serializers.ValidationError({'password':'Password is required to create a faculty login account.'})
            user=User(email=email,role='FACULTY');user.set_password(password);self._sync_user_name(user,name);user.save()
        elif user:
            if password: user.set_password(password)
            if name: self._sync_user_name(user,name)
            user.save()
        return user
    def create(self,validated):
        with transaction.atomic():
            email=validated.get('email'); user=self._account(validated); return Faculty.objects.create(user=user,email=email or None,**validated)
    def update(self,instance,validated):
        with transaction.atomic():
            email=validated.pop('email', instance.email); user=self._account(validated,instance); instance.user=user; instance=super().update(instance,validated); sync_faculty_email(instance,email); return instance
class FacultyAvailabilitySerializer(FriendlyModelSerializer):
    class Meta: model = FacultyAvailability; fields = '__all__'
class CourseOfferingFacultySerializer(FriendlyModelSerializer):
    class Meta: model = CourseOfferingFaculty; fields = '__all__'
class RoomAllowedYearField(serializers.IntegerField):
    def to_internal_value(self, data):
        if data is None or data == '' or (isinstance(data, str) and data.strip().upper() == 'ALL'):
            return None
        value = super().to_internal_value(data)
        if value not in (1, 2, 3, 4):
            self.fail('invalid')
        return value
    default_error_messages = {'invalid': 'Allowed year must be ALL or a year from 1 to 4.'}
class RoomReservedYearField(serializers.IntegerField):
    default_error_messages = {'invalid': 'Reserved year must be a year from 1 to 4.'}
    def to_internal_value(self, data):
        if data is None or data == '': return None
        value=super().to_internal_value(data)
        if value not in (1,2,3,4): self.fail('invalid')
        return value
class RoomSerializer(FriendlyModelSerializer):
    projector = serializers.SerializerMethodField()
    room_type_display = serializers.SerializerMethodField()
    allowed_year = RoomAllowedYearField(required=False, allow_null=True)
    reserved_year = RoomReservedYearField(required=False, allow_null=True)
    reserved_program_name = serializers.CharField(source='reserved_program.name',read_only=True,allow_null=True)
    class Meta: model = Room; fields = '__all__'
    def get_projector(self,obj): return 'Yes' if obj.has_projector else 'No'
    def get_room_type_display(self,obj): return obj.get_room_type_display()
class RoomAvailabilitySerializer(FriendlyModelSerializer):
    class Meta: model = RoomAvailability; fields = '__all__'
class TimeSlotTemplateSerializer(FriendlyModelSerializer):
    class Meta: model = TimeSlotTemplate; fields = '__all__'
class TimeSlotSerializer(FriendlyModelSerializer):
    class Meta: model = TimeSlot; fields = '__all__'
