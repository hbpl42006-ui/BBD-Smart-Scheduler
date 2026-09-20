import uuid
from django.db import models
from accounts.models import User
from institutions.models import Institution, Department
from academics.models import AcademicSession, Semester, Section, CourseOffering
from faculty.models import Faculty
from rooms.models import Room
from common.models import TimeSlot

class Timetable(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); institution=models.ForeignKey(Institution,on_delete=models.CASCADE,related_name='timetables'); academic_session=models.ForeignKey(AcademicSession,on_delete=models.CASCADE,related_name='timetables'); semester=models.ForeignKey(Semester,on_delete=models.CASCADE,related_name='timetables'); department=models.ForeignKey(Department,on_delete=models.CASCADE,related_name='timetables'); title=models.CharField(max_length=255); effective_date=models.DateField(null=True,blank=True); created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='created_timetables'); active=models.BooleanField(default=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
class TimetableVersion(models.Model):
    class Status(models.TextChoices): DRAFT='DRAFT'; PENDING_APPROVAL='PENDING_APPROVAL'; APPROVED='APPROVED'; PUBLISHED='PUBLISHED'; SUPERSEDED='SUPERSEDED'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); timetable=models.ForeignKey(Timetable,on_delete=models.CASCADE,related_name='versions'); version_no=models.PositiveIntegerField(); status=models.CharField(max_length=30,choices=Status.choices,default=Status.DRAFT); previous_version=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='clones'); created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='created_timetable_versions'); notes=models.TextField(blank=True); published_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='published_timetable_versions'); published_at=models.DateTimeField(null=True,blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['timetable','version_no'],name='unique_timetable_version')]
class ScheduleEntry(models.Model):
    class EntryType(models.TextChoices): LECTURE='LECTURE'; TUTORIAL='TUTORIAL'; PRACTICAL='PRACTICAL'; COMMON='COMMON'; LIBRARY='LIBRARY'; OTHER='OTHER'
    class Weekday(models.IntegerChoices): MONDAY=0; TUESDAY=1; WEDNESDAY=2; THURSDAY=3; FRIDAY=4; SATURDAY=5
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); version=models.ForeignKey(TimetableVersion,on_delete=models.CASCADE,related_name='entries'); section=models.ForeignKey(Section,on_delete=models.PROTECT,related_name='schedule_entries'); course_offering=models.ForeignKey(CourseOffering,on_delete=models.PROTECT,related_name='schedule_entries'); weekday=models.PositiveSmallIntegerField(choices=Weekday.choices); start_slot=models.ForeignKey(TimeSlot,on_delete=models.PROTECT,related_name='schedule_entries'); block_length=models.PositiveIntegerField(default=1); room=models.ForeignKey(Room,null=True,blank=True,on_delete=models.PROTECT,related_name='schedule_entries'); entry_type=models.CharField(max_length=20,choices=EntryType.choices,default=EntryType.LECTURE); locked=models.BooleanField(default=False); note=models.TextField(blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
class ScheduleEntryFaculty(models.Model):
    class Role(models.TextChoices): PRIMARY='PRIMARY'; CO_FACULTY='CO_FACULTY'; ASSISTANT='ASSISTANT'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); schedule_entry=models.ForeignKey(ScheduleEntry,on_delete=models.CASCADE,related_name='faculty_assignments'); faculty=models.ForeignKey(Faculty,on_delete=models.PROTECT,related_name='schedule_assignments'); role=models.CharField(max_length=20,choices=Role.choices,default=Role.PRIMARY)
    class Meta: constraints=[models.UniqueConstraint(fields=['schedule_entry','faculty'],name='unique_entry_faculty')]
