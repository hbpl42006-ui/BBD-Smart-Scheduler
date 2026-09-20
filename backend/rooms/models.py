from django.db import models
import uuid

class Room(models.Model):
    class RoomType(models.TextChoices):
        CLASSROOM = 'CLASSROOM', 'Classroom'
        COMPUTER_LAB = 'COMPUTER_LAB', 'Computer Lab'
        LAB = 'LAB', 'Lab'
        SEMINAR_HALL = 'SEMINAR_HALL', 'Seminar Hall'
        AUDITORIUM = 'AUDITORIUM', 'Auditorium'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    building = models.CharField(max_length=100)
    floor = models.CharField(max_length=50)
    capacity = models.PositiveIntegerField()
    room_type = models.CharField(max_length=50, choices=RoomType.choices, default=RoomType.CLASSROOM)
    facilities = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} ({self.building})"

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
