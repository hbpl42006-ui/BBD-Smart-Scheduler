from django.db import models
import uuid

class Room(models.Model):
    class RoomType(models.TextChoices):
        CLASSROOM = 'CLASSROOM', 'Classroom'
        COMPUTER_LAB = 'COMPUTER_LAB', 'Computer Lab'
        LAB = 'LAB', 'Lab'
        SEMINAR_HALL = 'SEMINAR_HALL', 'Seminar Hall'
        AUDITORIUM = 'AUDITORIUM', 'Auditorium'
        ELECTRICAL_LAB = 'ELECTRICAL_LAB', 'Electrical Lab'
        WORKSHOP = 'WORKSHOP', 'Workshop'
        MECHANICS_LAB = 'MECHANICS_LAB', 'Mechanics Lab'
        ENGINEERING_GRAPHICS_LAB = 'ENGINEERING_GRAPHICS_LAB', 'Engineering Graphics Lab'
        PHYSICS_LAB = 'PHYSICS_LAB', 'Physics Lab'
        SUSTAINABLE_CHEMICAL_SCIENCES_LAB = 'SUSTAINABLE_CHEMICAL_SCIENCES_LAB', 'Sustainable Chemical Sciences Lab'
        QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB = 'QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB', 'Quantum Physics and Advanced Functional Materials Lab'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    building = models.CharField(max_length=100)
    floor = models.CharField(max_length=50)
    capacity = models.PositiveIntegerField()
    room_type = models.CharField(max_length=50, choices=RoomType.choices, default=RoomType.CLASSROOM)
    facilities = models.JSONField(default=dict, blank=True)
    has_projector = models.BooleanField(default=False)
    allowed_year = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Null means available to all academic years.')
    reserved_program = models.ForeignKey('institutions.Program', null=True, blank=True, on_delete=models.SET_NULL, related_name='reserved_rooms')
    reserved_year = models.PositiveSmallIntegerField(null=True, blank=True)
    exclusive_reservation = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} ({self.building})"

    def is_allowed_for_year(self, year):
        return self.allowed_year is None or self.allowed_year == year

    def is_allowed_for_section(self, section):
        if not self.is_allowed_for_year(section.year):
            return False
        if not self.exclusive_reservation:
            return True
        return bool(self.reserved_program_id and self.reserved_program_id == section.program_id and self.reserved_year == section.year)


class RoomEligibilityException(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='eligibility_exceptions')
    program = models.ForeignKey('institutions.Program', on_delete=models.CASCADE, related_name='room_eligibility_exceptions')
    year = models.PositiveSmallIntegerField()
    course = models.ForeignKey('academics.Course', null=True, blank=True, on_delete=models.CASCADE, related_name='room_eligibility_exceptions')
    allow = models.BooleanField(default=True)
    reason = models.CharField(max_length=500, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('room', 'program', 'year', 'course'),
                name='unique_room_eligibility_exception_scope',
            ),
        ]

    def __str__(self):
        course = f' / {self.course.code}' if self.course_id else ''
        return f'{self.room.code} / {self.program.code} / Year {self.year}{course}'

class RoomAvailability(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        BLOCKED = 'BLOCKED', 'Blocked'
        MAINTENANCE = 'MAINTENANCE', 'Maintenance'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='availabilities')
    weekday = models.IntegerField(null=True, blank=True, help_text="0 for Monday, 6 for Sunday")
    date = models.DateField(null=True, blank=True)
    time_slot = models.ForeignKey('common.TimeSlot', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    reason = models.CharField(max_length=255, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.room.code} - {self.status}"
