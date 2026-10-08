from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User
from django.db import transaction
from faculty.email_service import sync_faculty_email

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if hasattr(obj, 'faculty_profile'):
                sync_faculty_email(obj.faculty_profile, obj.email)
    ordering = ('first_name',)
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_active', 'is_staff', 'is_superuser')
    search_fields = ('email', 'first_name', 'last_name')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'employee_id', 'role', 'management_scope', 'managed_departments')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'created_at', 'updated_at')}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2', 'role', 'management_scope', 'managed_departments', 'is_staff', 'is_active')}),)
    readonly_fields = ('last_login', 'created_at', 'updated_at')
