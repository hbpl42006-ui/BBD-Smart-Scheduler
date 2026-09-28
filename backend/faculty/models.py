from django.db import models
from accounts.models import User
from institutions.models import Department
import uuid

class Faculty(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='faculty_profile', null=True, blank=True)
    name = models.CharField(max_length=255, blank=True, default='')
    email = models.EmailField(blank=True, null=True)
    employee_code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    initials = models.CharField(max_length=10, blank=True, default='')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='faculties')
    active = models.BooleanField(default=True)
    max_daily_periods = models.PositiveIntegerField(default=4)
    max_weekly_periods = models.PositiveIntegerField(default=16)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        name=f'{self.user.first_name} {self.user.last_name}'.strip() if self.user else ''
        return f"{name or self.initials or self.employee_code} ({self.initials})"

class FacultyAvailability(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='availabilities')
    weekday = models.PositiveIntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')])
    time_slot = models.ForeignKey('common.TimeSlot', on_delete=models.CASCADE)
    is_available = models.BooleanField(default=True)
    preference_weight = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('faculty', 'weekday', 'time_slot')

    def __str__(self):
        return f"{self.faculty.initials} - Day {self.weekday} - Slot {self.time_slot.label}"

class CourseOfferingFaculty(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_offering = models.ForeignKey('academics.CourseOffering', on_delete=models.CASCADE, related_name='faculties')
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='course_offerings')
    role = models.CharField(max_length=50, default='PRIMARY')
    priority = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('course_offering', 'faculty')

    def __str__(self):
        return f"{self.faculty.initials} for {self.course_offering}"

class FacultyArrangement(models.Model):
    class Status(models.TextChoices):
        ASSIGNED='ASSIGNED'; COMPLETED='COMPLETED'; CANCELLED='CANCELLED'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    schedule_entry=models.ForeignKey('scheduling.ScheduleEntry',on_delete=models.PROTECT,related_name='arrangements')
    arrangement_date=models.DateField()
    absent_faculty=models.ForeignKey(Faculty,on_delete=models.PROTECT,related_name='absence_arrangements')
    substitute_faculty=models.ForeignKey(Faculty,on_delete=models.PROTECT,related_name='substitute_arrangements')
    reason=models.TextField(blank=True)
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.ASSIGNED)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='created_faculty_arrangements')
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True); completed_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['schedule_entry','arrangement_date'],name='unique_arrangement_entry_date')]
        indexes=[models.Index(fields=['arrangement_date','status']),models.Index(fields=['absent_faculty','arrangement_date']),models.Index(fields=['substitute_faculty','arrangement_date'])]

class ArrangementAttendanceEvidence(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    arrangement=models.ForeignKey(FacultyArrangement,on_delete=models.PROTECT,related_name='evidence')
    file=models.FileField(upload_to='private/arrangement-evidence/%Y/%m/')
    uploaded_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='uploaded_arrangement_evidence')
    uploaded_at=models.DateTimeField(auto_now_add=True)
    original_filename=models.CharField(max_length=255); mime_type=models.CharField(max_length=100); file_size=models.PositiveIntegerField()
