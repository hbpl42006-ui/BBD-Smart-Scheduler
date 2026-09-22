import csv
from io import BytesIO, StringIO
from openpyxl import Workbook
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from common.permissions import RolePermission
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter, OpenApiTypes
from rest_framework import serializers
from .services import reporting
from .serializers import ReportRowSerializer

MANAGEMENT = {'SUPER_ADMIN','ACADEMIC_ADMIN','HOD_OR_DEAN_APPROVER','TIMETABLE_COORDINATOR'}

def export_response(name, rows, fmt):
    if fmt == 'csv':
        output=StringIO(); fields=sorted({k for row in rows for k in row if not isinstance(row.get(k), (list,dict,set))}); writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader();writer.writerows([{k:row.get(k,'') for k in fields} for row in rows]);return HttpResponse(output.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{name}.csv"'})
    if fmt == 'xlsx':
        wb=Workbook();ws=wb.active;ws.title=name[:31];fields=sorted({k for row in rows for k in row if not isinstance(row.get(k),(list,dict,set))});ws.append(fields)
        for row in rows: ws.append([row.get(k,'') for k in fields])
        from copy import copy
        for cell in ws[1]:
            font=copy(cell.font);font.bold=True;cell.font=font
        for column in ws.columns: ws.column_dimensions[column[0].column_letter].width=min(max(max(len(str(x.value or '')) for x in column)+2,12),40)
        stream=BytesIO();wb.save(stream);return HttpResponse(stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{name}.xlsx"'})
    return None

class ReportView(APIView):
    permission_classes=[IsAuthenticated]
    report_name='report'; builder=None
    @extend_schema(description='Data-derived report. Use export=csv or export=xlsx for file output.', parameters=[OpenApiParameter('export',OpenApiTypes.STR,OpenApiParameter.QUERY,enum=['csv','xlsx']),OpenApiParameter('version',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('section',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('faculty',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('room',OpenApiTypes.UUID,OpenApiParameter.QUERY),OpenApiParameter('semester',OpenApiTypes.UUID,OpenApiParameter.QUERY)], responses=ReportRowSerializer(many=True))
    def get(self,request):
        if request.user.role == 'FACULTY' and self.report_name not in ('faculty-timetable','faculty-workload'): return Response({'detail':'You do not have permission to access this report.'},403)
        if request.user.role == 'FACULTY' and self.report_name=='faculty-workload' and request.query_params.get('faculty') and str(request.query_params['faculty']) != str(getattr(getattr(request.user,'faculty_profile',None),'pk',None)): return Response({'detail':'You may only view your own workload.'},403)
        rows=self.builder(request); fmt=(request.query_params.get('export') or request.query_params.get('format','')).lower(); exported=export_response(self.report_name,rows,fmt) if isinstance(rows,list) and fmt in ('csv','xlsx') else None
        return exported or Response(rows)

class FacultyWorkloadView(ReportView): report_name='faculty-workload'; builder=staticmethod(reporting.faculty_workload)
class RoomUtilizationView(ReportView): report_name='room-utilization'; builder=staticmethod(reporting.room_utilization)
class SectionTimetableReportView(ReportView): report_name='section-timetable'; builder=staticmethod(reporting.section_timetable)
class FacultyTimetableReportView(ReportView): report_name='faculty-timetable'; builder=staticmethod(reporting.faculty_timetable)
class RoomTimetableView(ReportView): report_name='room-timetable'; builder=staticmethod(reporting.room_timetable)
class CourseAllocationView(ReportView): report_name='course-allocation'; builder=staticmethod(reporting.course_allocation)
class UnscheduledView(ReportView): report_name='unscheduled'; builder=staticmethod(reporting.unscheduled)
class ConflictsView(ReportView): report_name='conflicts'; builder=staticmethod(reporting.conflicts)
class VersionActivityView(ReportView): report_name='version-activity'; builder=staticmethod(reporting.version_activity)
class AnalyticsView(ReportView):
    report_name='analytics'; builder=staticmethod(reporting.analytics)
    @extend_schema(responses=inline_serializer(name='ReportAnalytics',fields={'total_faculty':serializers.IntegerField(),'total_rooms':serializers.IntegerField(),'total_sections':serializers.IntegerField(),'scheduled_classes':serializers.IntegerField(),'room_utilization_percentage':serializers.FloatField(),'average_faculty_workload':serializers.FloatField(),'under_scheduled_courses':serializers.IntegerField(),'weekday_load':serializers.DictField(),'time_slot_load':serializers.DictField()}))
    def get(self,request): return super().get(request)
class FreeRoomsView(ReportView): report_name='free-rooms'; builder=staticmethod(reporting.free_rooms)

class HealthView(APIView):
    permission_classes=[]
    authentication_classes=[]
    @extend_schema(responses=inline_serializer(name='HealthResponse', fields={'status': serializers.CharField()}))
    def get(self, request):
        return Response({'status':'ok'})
