from django.contrib import admin
from .models import Timetable, TimetableVersion, ScheduleEntry, ScheduleEntryFaculty, GenerationRun

@admin.register(Timetable)
class TimetableAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'academic_session', 'semester', 'created_by', 'active'); search_fields = ('title',); list_filter = ('active', 'department'); autocomplete_fields = ('institution', 'academic_session', 'semester', 'department', 'created_by')
@admin.register(TimetableVersion)
class TimetableVersionAdmin(admin.ModelAdmin):
    list_display = ('timetable', 'version_no', 'status', 'entry_count', 'created_at', 'created_by', 'published_at')
    search_fields = ('timetable__title',); list_filter = ('status',); ordering = ('-created_at',); autocomplete_fields = ('timetable', 'previous_version', 'created_by', 'submitted_by', 'approved_by', 'rejected_by', 'published_by')
    readonly_fields = ('created_at', 'updated_at', 'entry_count')
    @admin.display(description='Entries')
    def entry_count(self, obj): return obj.entries.count()
@admin.register(ScheduleEntry)
class ScheduleEntryAdmin(admin.ModelAdmin):
    list_display = ('version', 'section', 'course_offering', 'weekday', 'start_slot', 'block_length', 'room', 'entry_type', 'locked')
    search_fields = ('section__name', 'course_offering__course__code'); list_filter = ('entry_type', 'locked', 'weekday'); autocomplete_fields = ('version', 'section', 'course_offering', 'start_slot', 'room')
@admin.register(ScheduleEntryFaculty)
class ScheduleEntryFacultyAdmin(admin.ModelAdmin):
    list_display = ('schedule_entry', 'faculty', 'role'); list_filter = ('role',); autocomplete_fields = ('schedule_entry', 'faculty')
@admin.register(GenerationRun)
class GenerationRunAdmin(admin.ModelAdmin):
    list_display = ('source_version', 'mode', 'status', 'solver_status', 'objective_score', 'applied_version', 'created_at')
    search_fields = ('source_version__timetable__title',); list_filter = ('mode', 'status', 'solver_status'); ordering = ('-created_at',); autocomplete_fields = ('timetable', 'source_version', 'created_by', 'applied_version')
    readonly_fields = ('created_at', 'started_at', 'completed_at', 'applied_at', 'objective_score', 'source_fingerprint', 'result', 'diagnostics', 'statistics', 'input_snapshot')
