"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from common.api import import_data
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from common.api import *

router = DefaultRouter()
for prefix, cls in [('institutions',InstitutionViewSet),('departments',DepartmentViewSet),('programs',ProgramViewSet),('academic-sessions',AcademicSessionViewSet),('semesters',SemesterViewSet),('sections',SectionViewSet),('courses',CourseViewSet),('course-offerings',CourseOfferingViewSet),('faculty',FacultyViewSet),('faculty-availability',FacultyAvailabilityViewSet),('rooms',RoomViewSet),('room-availability',RoomAvailabilityViewSet),('time-slot-templates',TimeSlotTemplateViewSet),('time-slots',TimeSlotViewSet)]: router.register(prefix, cls)
urlpatterns = [path('admin/', admin.site.urls), path('api/schema/', SpectacularAPIView.as_view(), name='schema'), path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'), path('api/auth/login/', LoginView.as_view()), path('api/auth/refresh/', TokenRefreshView.as_view()), path('api/auth/me/', me), path('api/dashboard/summary/', dashboard_summary), path('api/', include(router.urls))]
for _kind in ('faculty','courses','course-offerings','sections','rooms'):
    urlpatterns += [path(f'api/imports/{_kind}/<str:action>/', import_data)]
