from rest_framework import serializers
from scheduling.models import Timetable,TimetableVersion,ScheduleEntry,ScheduleEntryFaculty
class TimetableSerializer(serializers.ModelSerializer):
    class Meta: model=Timetable; fields='__all__'
class TimetableVersionSerializer(serializers.ModelSerializer):
    entry_count=serializers.IntegerField(source='entries.count',read_only=True)
    class Meta: model=TimetableVersion; fields='__all__'
class ScheduleEntrySerializer(serializers.ModelSerializer):
    course=serializers.SerializerMethodField(); section_name=serializers.CharField(source='section.name',read_only=True); room_code=serializers.CharField(source='room.code',read_only=True)
    class Meta: model=ScheduleEntry; fields='__all__'; extra_fields=('course','section_name','room_code')
    def get_course(self,obj): return {'id':str(obj.course_offering.course_id),'code':obj.course_offering.course.code,'name':obj.course_offering.course.name,'short_code':obj.course_offering.course.short_code}
class ScheduleEntryFacultySerializer(serializers.ModelSerializer):
    class Meta: model=ScheduleEntryFaculty; fields='__all__'
