from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils.translation import gettext_lazy as _
import uuid

class Role(models.TextChoices):
    SUPER_ADMIN = 'SUPER_ADMIN', _('Super Admin')
    ACADEMIC_ADMIN = 'ACADEMIC_ADMIN', _('Academic Admin')
    TIMETABLE_COORDINATOR = 'TIMETABLE_COORDINATOR', _('Timetable Coordinator')
    HOD_OR_DEAN_APPROVER = 'HOD_OR_DEAN_APPROVER', _('HOD or Dean Approver')
    CLASS_COORDINATOR = 'CLASS_COORDINATOR', _('Class Coordinator')
    FACULTY = 'FACULTY', _('Faculty')
    ROOM_LAB_COORDINATOR = 'ROOM_LAB_COORDINATOR', _('Room/Lab Coordinator')
    READ_ONLY_VIEWER = 'READ_ONLY_VIEWER', _('Read Only Viewer')

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_('The Email must be set'))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', Role.SUPER_ADMIN)
        return self.create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_('email address'), unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    employee_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, default='')
    role = models.CharField(max_length=50, choices=Role.choices, default=Role.READ_ONLY_VIEWER)
    
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email
