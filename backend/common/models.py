from django.db import models
from institutions.models import Institution
import uuid

class TimeSlotTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name='time_slot_templates')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class TimeSlot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(TimeSlotTemplate, on_delete=models.CASCADE, related_name='slots')
    label = models.CharField(max_length=50, help_text="e.g., 09:00-10:00")
    start_time = models.TimeField()
    end_time = models.TimeField()
    order = models.PositiveIntegerField()
    is_break = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.label} ({'Break' if self.is_break else 'Period'})"
