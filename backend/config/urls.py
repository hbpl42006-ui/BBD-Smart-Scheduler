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
from scheduling.views import TimetableList,VersionList,EntryList,EntryDetail,ValidateVersion,ValidateEntry,EntryLock,EntryUnlock,SectionTimetable,FacultyTimetable,MyFacultyTimetable,RoomAllocation,AvailableRooms,VersionLifecycle,VersionAudit,ApprovalQueue,VersionReview,CreateDraft,VersionCompare
from scheduling.generation_views import GenerationPreflight,GenerationRunList,GenerationRunDetail,GenerationApply
from notifications.views import NotificationList,NotificationUnreadCount,NotificationRead,NotificationMarkAllRead,NotificationPreferences,WhatsAppWebhook

router = DefaultRouter()
for prefix, cls in [('institutions',InstitutionViewSet),('departments',DepartmentViewSet),('programs',ProgramViewSet),('academic-sessions',AcademicSessionViewSet),('semesters',SemesterViewSet),('sections',SectionViewSet),('courses',CourseViewSet),('course-offerings',CourseOfferingViewSet),('faculty',FacultyViewSet),('faculty-availability',FacultyAvailabilityViewSet),('rooms',RoomViewSet),('room-availability',RoomAvailabilityViewSet),('time-slot-templates',TimeSlotTemplateViewSet),('time-slots',TimeSlotViewSet)]: router.register(prefix, cls)
router.register('room-availabilities', RoomAvailabilityViewSet, basename='roomavailabilities')
urlpatterns = [path('admin/', admin.site.urls), path('api/schema/', SpectacularAPIView.as_view(), name='schema'), path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'), path('api/auth/login/', LoginView.as_view()), path('api/auth/refresh/', TokenRefreshView.as_view()), path('api/auth/me/', me), path('api/dashboard/summary/', dashboard_summary), path('api/', include(router.urls))]
for _kind in ('faculty','courses','course-offerings','sections','rooms'):
    urlpatterns += [path(f'api/imports/{_kind}/<str:action>/', import_data)]
urlpatterns += [path('api/me/faculty-timetable/',MyFacultyTimetable.as_view()),path('api/timetables/',TimetableList.as_view()),path('api/timetables/<uuid:timetable_id>/versions/',VersionList.as_view()),path('api/versions/<uuid:version_id>/entries/',EntryList.as_view()),path('api/versions/<uuid:version_id>/entries/<uuid:entry_id>/',EntryDetail.as_view()),path('api/versions/<uuid:version_id>/validate/',ValidateVersion.as_view()),path('api/versions/<uuid:version_id>/validate-entry/',ValidateEntry.as_view()),path('api/versions/<uuid:version_id>/section-timetable/',SectionTimetable.as_view()),path('api/versions/<uuid:version_id>/faculty-timetable/',FacultyTimetable.as_view()),path('api/versions/<uuid:version_id>/room-allocation/',RoomAllocation.as_view()),path('api/versions/<uuid:version_id>/available-rooms/',AvailableRooms.as_view()),path('api/entries/<uuid:entry_id>/lock/',EntryLock.as_view()),path('api/entries/<uuid:entry_id>/unlock/',EntryUnlock.as_view())]
urlpatterns += [path('api/versions/<uuid:version_id>/generation-preflight/',GenerationPreflight.as_view()),path('api/versions/<uuid:version_id>/generation-runs/',GenerationRunList.as_view()),path('api/generation-runs/<uuid:run_id>/',GenerationRunDetail.as_view()),path('api/generation-runs/<uuid:run_id>/apply/',GenerationApply.as_view())]
urlpatterns += [path('api/versions/<uuid:version_id>/submit-for-approval/',VersionLifecycle.as_view(action='submit')),path('api/versions/<uuid:version_id>/approve/',VersionLifecycle.as_view(action='approve')),path('api/versions/<uuid:version_id>/reject/',VersionLifecycle.as_view(action='reject')),path('api/versions/<uuid:version_id>/publish/',VersionLifecycle.as_view(action='publish')),path('api/versions/<uuid:version_id>/audit/',VersionAudit.as_view())]
urlpatterns += [path('api/timetable-approvals/', ApprovalQueue.as_view())]
urlpatterns += [path('api/versions/<uuid:version_id>/review/', VersionReview.as_view()),path('api/versions/<uuid:version_id>/history/', VersionAudit.as_view())]
urlpatterns += [path('api/versions/<uuid:version_id>/create-draft/', CreateDraft.as_view()),path('api/versions/compare/', VersionCompare.as_view())]
urlpatterns += [path('api/notifications/', NotificationList.as_view()),path('api/notifications/unread-count/', NotificationUnreadCount.as_view()),path('api/notifications/<uuid:notification_id>/read/', NotificationRead.as_view()),path('api/notifications/mark-all-read/', NotificationMarkAllRead.as_view()),path('api/notifications/preferences/',NotificationPreferences.as_view()),path('api/notifications/whatsapp/webhook/',WhatsAppWebhook.as_view())]
