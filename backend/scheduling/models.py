import uuid
from django.db import models
from django.core.exceptions import ValidationError
from accounts.models import User
from institutions.models import Institution, Department
from academics.models import AcademicSession, Semester, Section, CourseOffering
from faculty.models import Faculty
from rooms.models import Room
from common.models import TimeSlot

class Timetable(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); institution=models.ForeignKey(Institution,on_delete=models.CASCADE,related_name='timetables'); academic_session=models.ForeignKey(AcademicSession,on_delete=models.CASCADE,related_name='timetables'); semester=models.ForeignKey(Semester,on_delete=models.CASCADE,related_name='timetables'); department=models.ForeignKey(Department,on_delete=models.CASCADE,related_name='timetables'); title=models.CharField(max_length=255); effective_date=models.DateField(null=True,blank=True); created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='created_timetables'); active=models.BooleanField(default=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
class TimetableVersion(models.Model):
    class Status(models.TextChoices): DRAFT='DRAFT'; IN_REVIEW='IN_REVIEW'; APPROVED='APPROVED'; PUBLISHED='PUBLISHED'; ARCHIVED='ARCHIVED'; PENDING_APPROVAL='PENDING_APPROVAL'; SUPERSEDED='SUPERSEDED'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); timetable=models.ForeignKey(Timetable,on_delete=models.CASCADE,related_name='versions'); version_no=models.PositiveIntegerField(); status=models.CharField(max_length=30,choices=Status.choices,default=Status.DRAFT); previous_version=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='clones'); created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='created_timetable_versions'); notes=models.TextField(blank=True); submitted_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='submitted_timetable_versions'); submitted_at=models.DateTimeField(null=True,blank=True); approved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='approved_timetable_versions'); approved_at=models.DateTimeField(null=True,blank=True); rejected_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='rejected_timetable_versions'); rejected_at=models.DateTimeField(null=True,blank=True); rejection_reason=models.TextField(blank=True); published_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='published_timetable_versions'); published_at=models.DateTimeField(null=True,blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['timetable','version_no'],name='unique_timetable_version')]
class ScheduleEntry(models.Model):
    class EntryType(models.TextChoices): LECTURE='LECTURE'; TUTORIAL='TUTORIAL'; PRACTICAL='PRACTICAL'; COMMON='COMMON'; LIBRARY='LIBRARY'; OTHER='OTHER'
    class DeliveryMode(models.TextChoices): ONLINE='ONLINE'; OFFLINE='OFFLINE'
    class Weekday(models.IntegerChoices): MONDAY=0; TUESDAY=1; WEDNESDAY=2; THURSDAY=3; FRIDAY=4; SATURDAY=5
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); version=models.ForeignKey(TimetableVersion,on_delete=models.CASCADE,related_name='entries'); section=models.ForeignKey(Section,on_delete=models.PROTECT,related_name='schedule_entries'); course_offering=models.ForeignKey(CourseOffering,on_delete=models.PROTECT,related_name='schedule_entries'); weekday=models.PositiveSmallIntegerField(choices=Weekday.choices); start_slot=models.ForeignKey(TimeSlot,on_delete=models.PROTECT,related_name='schedule_entries'); block_length=models.PositiveIntegerField(default=1); room=models.ForeignKey(Room,null=True,blank=True,on_delete=models.PROTECT,related_name='schedule_entries'); entry_type=models.CharField(max_length=20,choices=EntryType.choices,default=EntryType.LECTURE); delivery_mode=models.CharField(max_length=10,choices=DeliveryMode.choices,default=DeliveryMode.OFFLINE); locked=models.BooleanField(default=False); note=models.TextField(blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    def save(self, *args, **kwargs):
        from scheduling.weekdays import WORKING_DAYS, WEEKEND_ERROR_CODE, WEEKEND_ERROR_MESSAGE
        existing_weekday = type(self).objects.filter(pk=self.pk).values_list('weekday', flat=True).first() if self.pk else None
        if self.weekday not in WORKING_DAYS and existing_weekday != self.weekday:
            raise ValidationError({'weekday': ValidationError(WEEKEND_ERROR_MESSAGE, code=WEEKEND_ERROR_CODE)})
        return super().save(*args, **kwargs)
class ScheduleEntryFaculty(models.Model):
    class Role(models.TextChoices): PRIMARY='PRIMARY'; CO_FACULTY='CO_FACULTY'; ASSISTANT='ASSISTANT'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); schedule_entry=models.ForeignKey(ScheduleEntry,on_delete=models.CASCADE,related_name='faculty_assignments'); faculty=models.ForeignKey(Faculty,on_delete=models.PROTECT,related_name='schedule_assignments'); role=models.CharField(max_length=20,choices=Role.choices,default=Role.PRIMARY)
    class Meta: constraints=[models.UniqueConstraint(fields=['schedule_entry','faculty'],name='unique_entry_faculty')]


class TemporarySchedulePlan(models.Model):
    """A date-bounded overlay on an immutable published timetable version."""
    class EventType(models.TextChoices):
        TRAINING='TRAINING','Training'; WORKSHOP='WORKSHOP','Workshop'; PLACEMENT='PLACEMENT','Placement'
        SEMINAR='SEMINAR','Seminar'; GUEST_LECTURE='GUEST_LECTURE','Guest lecture'
        EXAM_PREPARATION='EXAM_PREPARATION','Exam preparation'; SPECIAL_CLASS='SPECIAL_CLASS','Special class'; OTHER='OTHER','Other'
    class Status(models.TextChoices): DRAFT='DRAFT','Draft'; PUBLISHED='PUBLISHED','Published'; CANCELLED='CANCELLED','Cancelled'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    base_timetable_version=models.ForeignKey(TimetableVersion,on_delete=models.PROTECT,related_name='temporary_plans')
    title=models.CharField(max_length=200); event_type=models.CharField(max_length=24,choices=EventType.choices,default=EventType.OTHER)
    start_date=models.DateField(); end_date=models.DateField(); status=models.CharField(max_length=12,choices=Status.choices,default=Status.DRAFT)
    priority=models.PositiveSmallIntegerField(default=0); notes=models.TextField(blank=True)
    sections=models.ManyToManyField(Section,through='TemporarySchedulePlanSection',related_name='temporary_schedule_plans')
    created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='temporary_schedule_plans')
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True); published_at=models.DateTimeField(null=True,blank=True)
    published_impact=models.JSONField(default=dict,blank=True)
    class Meta: ordering=['-created_at']


class TemporarySchedulePlanSection(models.Model):
    plan=models.ForeignKey(TemporarySchedulePlan,on_delete=models.CASCADE,related_name='section_links')
    section=models.ForeignKey(Section,on_delete=models.CASCADE,related_name='temporary_plan_links')
    class Meta: constraints=[models.UniqueConstraint(fields=['plan','section'],name='unique_temp_plan_section')]


class TemporaryScheduleBlock(models.Model):
    plan=models.ForeignKey(TemporarySchedulePlan,on_delete=models.CASCADE,related_name='blocks')
    section=models.ForeignKey(Section,on_delete=models.CASCADE,related_name='temporary_schedule_blocks')
    specific_date=models.DateField(null=True,blank=True); weekday=models.PositiveSmallIntegerField(null=True,blank=True,choices=ScheduleEntry.Weekday.choices)
    start_slot=models.ForeignKey(TimeSlot,on_delete=models.PROTECT,related_name='temporary_schedule_blocks'); block_length=models.PositiveSmallIntegerField(default=1)
    event_type=models.CharField(max_length=24,choices=TemporarySchedulePlan.EventType.choices,default=TemporarySchedulePlan.EventType.OTHER)
    title=models.CharField(max_length=200); room=models.ForeignKey(Room,null=True,blank=True,on_delete=models.PROTECT,related_name='temporary_schedule_blocks')
    faculty=models.ManyToManyField(Faculty,blank=True,related_name='temporary_schedule_blocks'); trainer_name=models.CharField(max_length=200,blank=True)
    delivery_mode=models.CharField(max_length=10,choices=ScheduleEntry.DeliveryMode.choices,default=ScheduleEntry.DeliveryMode.OFFLINE)
    override_regular_class=models.BooleanField(default=False); notes=models.TextField(blank=True)
    class Meta: ordering=['start_slot__order','title']

class GenerationRun(models.Model):
    class Status(models.TextChoices): PENDING='PENDING'; RUNNING='RUNNING'; SUCCEEDED='SUCCEEDED'; INFEASIBLE='INFEASIBLE'; FAILED='FAILED'; APPLIED='APPLIED'
    class SolverStatus(models.TextChoices): OPTIMAL='OPTIMAL'; FEASIBLE='FEASIBLE'; INFEASIBLE='INFEASIBLE'; MODEL_INVALID='MODEL_INVALID'; UNKNOWN='UNKNOWN'; PRECHECK_FAILED='PRECHECK_FAILED'; POST_VALIDATION_FAILED='POST_VALIDATION_FAILED'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    timetable=models.ForeignKey(Timetable,on_delete=models.CASCADE,related_name='generation_runs')
    source_version=models.ForeignKey(TimetableVersion,on_delete=models.PROTECT,related_name='generation_runs')
    created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='generation_runs')
    mode=models.CharField(max_length=40,default='FILL_GAPS')
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.PENDING)
    solver_status=models.CharField(max_length=32,choices=SolverStatus.choices,blank=True)
    input_config=models.JSONField(default=dict); input_snapshot=models.JSONField(default=dict); input_snapshot_version=models.PositiveSmallIntegerField(null=True,blank=True); input_fingerprint=models.CharField(max_length=64,blank=True); generation_validation=models.JSONField(default=dict,blank=True); current_validation=models.JSONField(default=dict,blank=True); apply_validation=models.JSONField(default=dict,blank=True); result=models.JSONField(default=dict); diagnostics=models.JSONField(default=dict); statistics=models.JSONField(default=dict)
    objective_score=models.FloatField(null=True,blank=True); source_fingerprint=models.CharField(max_length=64,blank=True)
    created_at=models.DateTimeField(auto_now_add=True); started_at=models.DateTimeField(null=True,blank=True); completed_at=models.DateTimeField(null=True,blank=True); applied_at=models.DateTimeField(null=True,blank=True)
    applied_version=models.ForeignKey(TimetableVersion,null=True,blank=True,on_delete=models.PROTECT,related_name='applied_generation_runs'); error_message=models.TextField(blank=True)
    def save(self,*args,**kwargs):
        if self.pk:
            stored=type(self).objects.filter(pk=self.pk).values('input_snapshot','input_snapshot_version','input_fingerprint').first()
            if stored and stored['input_snapshot'] and any(stored[key]!=getattr(self,key) for key in ('input_snapshot','input_snapshot_version','input_fingerprint')):
                raise ValidationError('A generation run input snapshot is immutable once recorded.')
        return super().save(*args,**kwargs)
