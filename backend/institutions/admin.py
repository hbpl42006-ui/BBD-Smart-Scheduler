from django.contrib import admin
from .models import Institution, Department, Program

@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'timezone'); search_fields = ('name', 'code'); ordering = ('name',)
@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'institution'); search_fields = ('name', 'code'); list_filter = ('institution',); autocomplete_fields = ('institution',)
@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'department', 'duration_years'); search_fields = ('name', 'code'); list_filter = ('department',); autocomplete_fields = ('department',)
