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
import time
class GenerationPreflight(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(request=GenerationConfigSerializer,responses={200: GenerationPreflightResponseSerializer, 400: GenerationPreflightResponseSerializer})
    def post(self,request,version_id):
        print(f"[GEN PREFLIGHT] request received method={request.method} path={request.path} version_id={version_id}", flush=True)
        from django.shortcuts import get_object_or_404
        result = preflight_generation(get_object_or_404(TimetableVersion, pk=version_id),request.data)
        return Response(result, status=200 if result.get('valid') else 400)
class GenerationRunList(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(responses=GenerationRunSerializer(many=True))
    def get(self,request,version_id):return Response(GenerationRunSerializer(GenerationRun.objects.filter(source_version_id=version_id),many=True).data)
    @extend_schema(request=GenerationConfigSerializer,responses=GenerationRunSerializer)
    def post(self,request,version_id):
        print(f"[GEN RUN] request received method={request.method} path={request.path} version_id={version_id}", flush=True)
        request_started=time.perf_counter()
        print('[GEN RUN] serializer start', flush=True)
        config_serializer=GenerationConfigSerializer(data=request.data)
        if not config_serializer.is_valid():
            print(f"[GEN RUN] serializer end valid=False elapsed={time.perf_counter()-request_started:.3f}s", flush=True)
            return Response(config_serializer.errors,status=400)
        config=dict(config_serializer.validated_data)
        if 'section_ids' in config:
            config['section_ids']=[str(section_id) for section_id in config['section_ids']]
        print(f"[GEN RUN] serializer end valid=True elapsed={time.perf_counter()-request_started:.3f}s", flush=True)
        from django.shortcuts import get_object_or_404
        v=get_object_or_404(TimetableVersion, pk=version_id)
        preflight_started=time.perf_counter()
        print('[GEN RUN] endpoint preflight start', flush=True)
        check = preflight_generation(v, config, include_internal=True)
        print(f"[GEN RUN] endpoint preflight end elapsed={time.perf_counter()-preflight_started:.3f}s valid={check.get('valid')} errors={len(check.get('errors',[]))}", flush=True)
        if not check.get('valid'):
            check.pop('_requirements', None)
            print(f"[GEN RUN] total elapsed={time.perf_counter()-request_started:.3f}s status=400 stage=endpoint_preflight", flush=True)
            return Response(check, 400)
        validated_requirements = check.pop('_requirements')
        run=GenerationRun.objects.create(timetable=v.timetable,source_version=v,created_by=request.user,mode=config.get('mode','FILL_GAPS'),input_config=config,source_fingerprint=fingerprint(v));record('GENERATION_STARTED',request.user,timetable=v.timetable,version=v,entity_type='GenerationRun',entity_id=run.id)
        generated_run=run_generation(run,validated_requirements=validated_requirements)
        print('[GEN RUN] response serialization start',flush=True)
        serialization_started=time.perf_counter()
        response_data=GenerationRunSerializer(generated_run).data
        print(f"[GEN RUN] response serialization end elapsed={time.perf_counter()-serialization_started:.3f}s",flush=True)
        print(f"[GEN RUN] total elapsed={time.perf_counter()-request_started:.3f}s status=201 solver_status={generated_run.solver_status}",flush=True)
        return Response(response_data,status=201)
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
