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
    class DeliveryPolicy(models.TextChoices):
        STANDARD = 'STANDARD', 'Standard (physical room required)'
        HYBRID = 'HYBRID', 'Hybrid (one offline weekday)'

    class Weekday(models.IntegerChoices):
        MONDAY = 0, 'Monday'
        TUESDAY = 1, 'Tuesday'
        WEDNESDAY = 2, 'Wednesday'
        THURSDAY = 3, 'Thursday'
        FRIDAY = 4, 'Friday'
        SATURDAY = 5, 'Saturday'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name='sections')
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='sections')
    year = models.PositiveIntegerField()
    name = models.CharField(max_length=50)
    student_strength = models.PositiveIntegerField(default=60)
    delivery_policy = models.CharField(max_length=12, choices=DeliveryPolicy.choices, default=DeliveryPolicy.STANDARD)
    offline_weekday = models.PositiveSmallIntegerField(choices=Weekday.choices, null=True, blank=True)
    coordinator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='coordinated_sections')
    coordinator_name = models.CharField(max_length=255, blank=True, default='')
    coordinator_mobile = models.CharField(max_length=25, blank=True, default='')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        from scheduling.weekdays import WORKING_DAYS
        if self.delivery_policy == self.DeliveryPolicy.HYBRID and self.offline_weekday is None:
            raise ValidationError({'offline_weekday': 'A hybrid section must have an offline weekday configured.'})
        if self.delivery_policy == self.DeliveryPolicy.HYBRID and self.offline_weekday not in WORKING_DAYS:
            raise ValidationError({'offline_weekday': 'Hybrid offline day must be a working day (Monday-Friday).'})
        if self.delivery_policy != self.DeliveryPolicy.HYBRID and self.offline_weekday is not None:
            raise ValidationError({'offline_weekday': 'Offline weekday is only valid for hybrid sections.'})

    def __str__(self):
        return f"{self.program.code} - {self.name} (Year {self.year})"

class SectionDeliveryPolicy(models.Model):
    """Delivery policy for a section in one specific academic period."""
    class Mode(models.TextChoices):
        STANDARD = 'STANDARD', 'Standard (physical room required)'
        HYBRID = 'HYBRID', 'Hybrid (one offline weekday)'

    class Source(models.TextChoices):
        OFFICIAL_TIMETABLE = 'OFFICIAL_TIMETABLE', 'Official timetable'
        ADMIN = 'ADMIN', 'Administrator'

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='period_delivery_policies')
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='section_delivery_policies')
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='section_delivery_policies')
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.HYBRID)
    offline_weekday = models.PositiveSmallIntegerField(choices=Section.Weekday.choices, null=True, blank=True)
    source = models.CharField(max_length=24, choices=Source.choices, default=Source.OFFICIAL_TIMETABLE)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['section', 'academic_session', 'semester'], name='unique_section_period_delivery_policy')]

    def clean(self):
        from django.core.exceptions import ValidationError
        from scheduling.weekdays import WORKING_DAYS
        if self.section_id and self.semester_id and self.section.semester_id != self.semester_id:
            raise ValidationError({'semester': 'Delivery policy semester must match the section semester.'})
        if self.academic_session_id and self.semester_id and self.semester.session_id != self.academic_session_id:
            raise ValidationError({'academic_session': 'Delivery policy session must match the semester session.'})
        if self.mode == self.Mode.HYBRID and self.offline_weekday not in WORKING_DAYS:
            raise ValidationError({'offline_weekday': 'Hybrid policies require an offline weekday from Monday through Friday.'})
        if self.mode != self.Mode.HYBRID and self.offline_weekday is not None:
            raise ValidationError({'offline_weekday': 'Offline weekday is only valid for HYBRID policies.'})

    def __str__(self):
        return f'{self.section} / {self.academic_session} / {self.semester}: {self.mode}'

class SectionWeeklyOffPolicy(models.Model):
    """Authoritative weekly-off source coverage for a section and period."""
    class PolicyType(models.TextChoices):
        WEEKLY_OFF = 'WEEKLY_OFF', 'Weekly OFF day'
        NO_WEEKLY_OFF = 'NO_WEEKLY_OFF', 'No weekly OFF day'

    class Source(models.TextChoices):
        OFFICIAL_TIMETABLE = 'OFFICIAL_TIMETABLE', 'Official timetable'
        ADMIN = 'ADMIN', 'Administrator'

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='weekly_off_policies')
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='section_weekly_off_policies')
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='section_weekly_off_policies')
    policy_type = models.CharField(max_length=20, choices=PolicyType.choices, default=PolicyType.WEEKLY_OFF)
    weekday = models.PositiveSmallIntegerField(choices=[(value, label) for value, label in Section.Weekday.choices if value < 5], null=True, blank=True)
    source = models.CharField(max_length=24, choices=Source.choices, default=Source.OFFICIAL_TIMETABLE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['section', 'academic_session', 'semester'], condition=models.Q(is_active=True), name='unique_active_section_weekly_off'),
            models.CheckConstraint(condition=(models.Q(policy_type='WEEKLY_OFF', weekday__isnull=False) | models.Q(policy_type='NO_WEEKLY_OFF', weekday__isnull=True)), name='weekly_off_type_matches_weekday'),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        from scheduling.weekdays import WORKING_DAYS
        if self.section_id and self.semester_id and self.section.semester_id != self.semester_id:
            raise ValidationError({'semester': 'Weekly OFF policy semester must match the section semester.'})
        if self.academic_session_id and self.semester_id and self.semester.session_id != self.academic_session_id:
            raise ValidationError({'academic_session': 'Weekly OFF policy session must match the semester session.'})
        if self.policy_type == self.PolicyType.WEEKLY_OFF and self.weekday not in WORKING_DAYS:
            raise ValidationError({'weekday': 'Weekly OFF day must be Monday through Friday.'})
        if self.policy_type == self.PolicyType.NO_WEEKLY_OFF and self.weekday is not None:
            raise ValidationError({'weekday': 'A NO_WEEKLY_OFF policy must not configure a weekday.'})

    def __str__(self):
        day = self.get_weekday_display() + ' OFF' if self.policy_type == self.PolicyType.WEEKLY_OFF else 'No weekly OFF day'
        return f'{self.section} / {self.academic_session} / {self.semester}: {day}'

class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    credit = models.DecimalField(max_digits=4, decimal_places=1)
    short_code = models.CharField(max_length=20)
    lecture_hours = models.PositiveIntegerField(default=0)
    tutorial_hours = models.PositiveIntegerField(default=0)
    practical_hours = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    
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
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='offerings')
    
    weekly_periods = models.PositiveIntegerField()
    default_class_type = models.CharField(max_length=20, choices=ClassType.choices, default=ClassType.LECTURE)
    required_block_size = models.PositiveIntegerField(default=1)
    allow_remainder_period = models.BooleanField(default=False)
    room_type_requirement = models.CharField(max_length=50, blank=True)
    preferred_room = models.ForeignKey('rooms.Room', on_delete=models.SET_NULL, null=True, blank=True)
    active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.course.code} for {self.section.name}"
