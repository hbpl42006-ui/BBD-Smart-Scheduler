from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.models import Role

WRITE_ROLES = {
    Role.SUPER_ADMIN, Role.ACADEMIC_ADMIN, Role.TIMETABLE_COORDINATOR,
    Role.ROOM_LAB_COORDINATOR,
}

class RolePermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.role in WRITE_ROLES

