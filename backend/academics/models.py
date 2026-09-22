from django.db import models
from institutions.models import Program
import uuid

class AcademicSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey('institutions.Institution', on_delete=models.CASCADE, related_name='academic_sessions')
    name = models.CharField(max_length=50, help_text="e.g., 2026-27")
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class Semester(models.Model):
    class SemesterType(models.TextChoices):
        ODD = 'ODD', 'Odd'
        EVEN = 'EVEN', 'Even'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='semesters')
    name = models.CharField(max_length=50)
    number = models.PositiveIntegerField()
    type = models.CharField(max_length=10, choices=SemesterType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.type})"

class Section(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name='sections')
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='sections')
    year = models.PositiveIntegerField()
    name = models.CharField(max_length=50)
    student_strength = models.PositiveIntegerField(default=60)
    coordinator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='coordinated_sections')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.program.code} - {self.name} (Year {self.year})"

class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    credit = models.DecimalField(max_digits=4, decimal_places=1)
    short_code = models.CharField(max_length=20)
    lecture_hours = models.PositiveIntegerField(default=0)
    tutorial_hours = models.PositiveIntegerField(default=0)
    practical_hours = models.PositiveIntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.code})"

class CourseOffering(models.Model):
    class ClassType(models.TextChoices):
        LECTURE = 'LECTURE', 'Lecture'
        TUTORIAL = 'TUTORIAL', 'Tutorial'
        PRACTICAL = 'PRACTICAL', 'Practical'
        COMMON = 'COMMON', 'Common'
        LIBRARY = 'LIBRARY', 'Library'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='course_offerings')
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='course_offerings')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='offerings')
    
    weekly_periods = models.PositiveIntegerField()
    default_class_type = models.CharField(max_length=20, choices=ClassType.choices, default=ClassType.LECTURE)
    required_block_size = models.PositiveIntegerField(default=1)
    room_type_requirement = models.CharField(max_length=50, blank=True)
    preferred_room = models.ForeignKey('rooms.Room', on_delete=models.SET_NULL, null=True, blank=True)
    active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.course.code} for {self.section.name}"
