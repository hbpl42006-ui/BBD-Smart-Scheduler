from django.contrib import admin
from .models import Room, RoomAvailability

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('room_number', 'building', 'floor', 'capacity', 'room_type', 'active')
    search_fields = ('code', 'building'); list_filter = ('room_type', 'building', 'active'); ordering = ('building', 'code')
    @admin.display(description='Room No.')
    def room_number(self, obj): return obj.code
@admin.register(RoomAvailability)
class RoomAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('room', 'weekday', 'date', 'time_slot', 'status', 'reason'); list_filter = ('status', 'weekday'); search_fields = ('room__code', 'reason'); autocomplete_fields = ('room', 'time_slot')
