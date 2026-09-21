from django.contrib import admin
from .models import AuditEvent

@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'actor', 'entity_type', 'entity_id', 'created_at')
    search_fields = ('event_type', 'entity_type', 'entity_id'); list_filter = ('event_type', 'entity_type'); ordering = ('-created_at',)
    readonly_fields = ('id', 'event_type', 'actor', 'timetable_id', 'version_id', 'entity_type', 'entity_id', 'old_data', 'new_data', 'metadata', 'created_at')
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
