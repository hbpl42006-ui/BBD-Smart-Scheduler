from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from scheduling.models import TimetableVersion,GenerationRun
from scheduling.services.audit import record
from scheduling.solver import preflight_generation,run_generation,apply_generation
from scheduling.solver.service import GenerationApplyError
from scheduling.solver.service import fingerprint
from scheduling.solver_serializers import GenerationRunSerializer, ErrorResponseSerializer, GenerationPreflightResponseSerializer, GenerationApplyResponseSerializer
from common.permissions import GenerationPermission
from drf_spectacular.utils import extend_schema
from scheduling.solver_serializers import GenerationConfigSerializer
class GenerationPreflight(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(request=GenerationConfigSerializer,responses={200: GenerationPreflightResponseSerializer, 400: GenerationPreflightResponseSerializer})
    def post(self,request,version_id):
        from django.shortcuts import get_object_or_404
        result = preflight_generation(get_object_or_404(TimetableVersion, pk=version_id),request.data)
        return Response(result, status=200 if result.get('valid') else 400)
class GenerationRunList(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(responses=GenerationRunSerializer(many=True))
    def get(self,request,version_id):return Response(GenerationRunSerializer(GenerationRun.objects.filter(source_version_id=version_id),many=True).data)
    @extend_schema(request=GenerationConfigSerializer,responses=GenerationRunSerializer)
    def post(self,request,version_id):
        from django.shortcuts import get_object_or_404
        v=get_object_or_404(TimetableVersion, pk=version_id)
        check = preflight_generation(v, request.data)
        if not check.get('valid'):
            return Response(check, 400)
        run=GenerationRun.objects.create(timetable=v.timetable,source_version=v,created_by=request.user,mode=request.data.get('mode','FILL_GAPS'),input_config=request.data,source_fingerprint=fingerprint(v));record('GENERATION_STARTED',request.user,timetable=v.timetable,version=v,entity_type='GenerationRun',entity_id=run.id);return Response(GenerationRunSerializer(run_generation(run)).data,status=201)
class GenerationRunDetail(APIView):
    permission_classes=[IsAuthenticated]
    @extend_schema(responses=GenerationRunSerializer)
    def get(self,request,run_id):return Response(GenerationRunSerializer(GenerationRun.objects.get(pk=run_id)).data)
class GenerationApply(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(request=None,responses={200: GenerationApplyResponseSerializer, 409: ErrorResponseSerializer})
    def post(self,request,run_id):
        try:new=apply_generation(GenerationRun.objects.get(pk=run_id));return Response({'applied_version':str(new.id),'version_no':new.version_no})
        except GenerationApplyError as e:return Response({'code':e.code,'message':e.message,'conflicts':e.conflicts,'error_count':len(e.conflicts),'warning_count':0},409)
