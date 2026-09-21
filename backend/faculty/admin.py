from django.contrib import admin
from .models import Faculty, FacultyAvailability, CourseOfferingFaculty

@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'employee_code', 'initials', 'department', 'linked_email', 'max_daily_periods', 'max_weekly_periods')
    search_fields = ('employee_code', 'initials', 'user__email', 'user__first_name', 'user__last_name')
    list_filter = ('department',); autocomplete_fields = ('user', 'department')
    @admin.display(description='Faculty name')
    def display_name(self, obj):
        return str(obj)
    @admin.display(description='Email')
    def linked_email(self, obj):
        return obj.user.email if obj.user else '—'
@admin.register(FacultyAvailability)
class FacultyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('faculty', 'weekday', 'time_slot', 'is_available', 'preference_weight'); list_filter = ('weekday', 'is_available'); autocomplete_fields = ('faculty', 'time_slot')
@admin.register(CourseOfferingFaculty)
class CourseOfferingFacultyAdmin(admin.ModelAdmin):
    list_display = ('course_offering', 'faculty', 'role', 'priority'); list_filter = ('role',); autocomplete_fields = ('course_offering', 'faculty')
