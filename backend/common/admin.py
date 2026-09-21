from django.contrib import admin
from .models import TimeSlotTemplate, TimeSlot

@admin.register(TimeSlotTemplate)
class TimeSlotTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution'); search_fields = ('name',); list_filter = ('institution',); autocomplete_fields = ('institution',)
@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ('label', 'template', 'start_time', 'end_time', 'order', 'is_break'); search_fields = ('label',); list_filter = ('template', 'is_break'); autocomplete_fields = ('template',)
