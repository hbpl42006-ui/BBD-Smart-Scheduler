from rest_framework import serializers
from scheduling.models import Timetable,TimetableVersion,ScheduleEntry,ScheduleEntryFaculty
from scheduling.weekdays import WORKING_DAYS, WEEKEND_ERROR_CODE
class TimetableSerializer(serializers.ModelSerializer):
    class Meta:
        model=Timetable; fields='__all__'; read_only_fields=('created_by','created_at','updated_at')
class TimetableVersionSerializer(serializers.ModelSerializer):
    entry_count=serializers.IntegerField(source='entries.count',read_only=True)
    class Meta: model=TimetableVersion; fields='__all__'; read_only_fields=('created_by','created_at','updated_at')
class ScheduleEntrySerializer(serializers.ModelSerializer):
    weekday=serializers.IntegerField()
    faculty_assignments=serializers.SerializerMethodField()
    course=serializers.SerializerMethodField(); section_name=serializers.CharField(source='section.name',read_only=True); room_code=serializers.CharField(source='room.code',read_only=True)
    class Meta: model=ScheduleEntry; fields='__all__'; extra_fields=('course','section_name','room_code')
    def validate(self, attrs):
        weekday=attrs.get('weekday',self.instance.weekday if self.instance else None)
        if weekday is not None and weekday not in WORKING_DAYS:
            raise serializers.ValidationError({'code':WEEKEND_ERROR_CODE,'weekday':'Classes can only be scheduled Monday through Friday.'})
        section=attrs.get('section',self.instance.section if self.instance else None)
        weekday=attrs.get('weekday',self.instance.weekday if self.instance else None)
        if section is not None and weekday is not None:
            from academics.delivery import expected_delivery_mode
            semester=section.semester
            attrs['delivery_mode']=expected_delivery_mode(section,weekday,semester)
        return attrs
    def get_course(self,obj): return {'id':str(obj.course_offering.course_id),'code':obj.course_offering.course.code,'name':obj.course_offering.course.name,'short_code':obj.course_offering.course.short_code,'credit':obj.course_offering.course.credit}
    def get_faculty_assignments(self,obj):
        assignments=[]
        for assignment in obj.faculty_assignments.all():
            faculty=assignment.faculty
            name=''
            if faculty.user:
                name=f'{faculty.user.first_name} {faculty.user.last_name}'.strip()
            name=name or faculty.name or faculty.initials or faculty.employee_code or 'Unnamed faculty'
            assignments.append({'faculty_id':str(assignment.faculty_id),'role':assignment.role,'name':name,'initials':faculty.initials})
        return assignments
class ScheduleEntryFacultySerializer(serializers.ModelSerializer):
    class Meta: model=ScheduleEntryFaculty; fields='__all__'
