from django.contrib import admin
from .models import AcademicSession, Semester, Section, SectionDeliveryPolicy, SectionWeeklyOffPolicy, Course, CourseOffering

@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution', 'start_date', 'end_date', 'is_active'); search_fields = ('name',); list_filter = ('institution', 'is_active'); autocomplete_fields = ('institution',)
@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ('name', 'number', 'type', 'session', 'start_date', 'end_date'); search_fields = ('name',); list_filter = ('type', 'session'); autocomplete_fields = ('session',)
@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'program', 'semester', 'year', 'delivery_policy', 'offline_weekday', 'student_strength', 'coordinator'); search_fields = ('name',); list_filter = ('year', 'program', 'semester', 'delivery_policy', 'offline_weekday'); autocomplete_fields = ('program', 'semester', 'coordinator')
@admin.register(SectionDeliveryPolicy)
class SectionDeliveryPolicyAdmin(admin.ModelAdmin):
    list_display = ('section', 'academic_session', 'semester', 'mode', 'offline_weekday', 'source', 'active')
    list_filter = ('academic_session', 'semester', 'mode', 'source', 'active')
    search_fields = ('section__name', 'section__program__code')
    autocomplete_fields = ('section', 'academic_session', 'semester')
@admin.register(SectionWeeklyOffPolicy)
class SectionWeeklyOffPolicyAdmin(admin.ModelAdmin):
    list_display = ('section', 'academic_session', 'semester', 'policy_type', 'weekday', 'source', 'is_active')
    list_filter = ('academic_session', 'semester', 'policy_type', 'weekday', 'source', 'is_active')
    search_fields = ('section__name', 'section__program__code')
    autocomplete_fields = ('section', 'academic_session', 'semester')
@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'short_code', 'credit'); search_fields = ('code', 'name', 'short_code'); ordering = ('code',)
@admin.register(CourseOffering)
class CourseOfferingAdmin(admin.ModelAdmin):
    list_display = ('course', 'section', 'semester', 'weekly_periods', 'default_class_type', 'allow_remainder_period', 'active'); search_fields = ('course__code', 'course__name', 'section__name'); list_filter = ('active', 'default_class_type', 'allow_remainder_period', 'semester'); autocomplete_fields = ('semester', 'section', 'course', 'preferred_room')
