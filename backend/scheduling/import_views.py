from django.http import HttpResponse
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from common.permissions import GenerationPermission
from scheduling.models import TimetableVersion
from scheduling.services.timetable_import import parse,commit,template
from scheduling.services.audit import record
from common.imports import rows_from_upload
import logging

logger=logging.getLogger(__name__)

class TimetableImportView(APIView):
    permission_classes=[GenerationPermission]
    def post(self,request,version_id,action):
        version=TimetableVersion.objects.get(pk=version_id)
        if version.status!='DRAFT': return Response({'detail':'Timetable import is allowed only for DRAFT versions.'},status=409)
        upload=request.FILES.get('file')
        if not upload or not upload.name.lower().endswith(('.csv','.xlsx')): return Response({'detail':'Upload a CSV or XLSX file.'},status=400)
        try: rows=rows_from_upload(upload); result,grouped=parse(version,rows)
        except Exception as exc:
            logger.exception('Timetable preview failed')
            return Response({'detail':'Timetable preview failed.','error_type':exc.__class__.__name__,'error':str(exc)},status=400)
        if action=='preview': return Response(result)
        if result['invalid']: return Response(result,status=400)
        with transaction.atomic():
            created=commit(version,grouped); record('TIMETABLE_IMPORTED',request.user,timetable=version.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk,metadata={'filename':upload.name,'entries_created':created['entries_created'],'failed_count':result['invalid']})
        return Response(created,status=201)

class TimetableImportTemplateView(APIView):
    permission_classes=[GenerationPermission]
    def get(self,request,version_id):
        version=TimetableVersion.objects.get(pk=version_id)
        if version.status!='DRAFT': return Response({'detail':'Templates are available only for DRAFT versions.'},status=409)
        return HttpResponse(template(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="timetable-import-template.xlsx"'})
