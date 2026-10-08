from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from scheduling.models import TimetableVersion,GenerationRun
from scheduling.services.audit import record
from scheduling.solver import preflight_generation,run_generation,apply_generation
from scheduling.solver.service import GenerationApplyError,validate_generation_apply
from scheduling.solver.service import fingerprint
from scheduling.solver_serializers import GenerationRunSerializer, ErrorResponseSerializer, GenerationPreflightResponseSerializer, GenerationApplyResponseSerializer
from common.permissions import GenerationPermission
from drf_spectacular.utils import extend_schema
from scheduling.solver_serializers import GenerationConfigSerializer
import time
from django.db import connection

class _QueryCounter:
    def __init__(self): self.count = 0
    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)
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
        source_load_started=time.perf_counter()
        v=get_object_or_404(TimetableVersion.objects.select_related('timetable'), pk=version_id)
        source_load_seconds=time.perf_counter()-source_load_started
        query_counter=_QueryCounter()
        with connection.execute_wrapper(query_counter):
            preflight_started=time.perf_counter()
            print('[GEN RUN] request start',flush=True)
            print('[GEN RUN] preflight start', flush=True)
            check = preflight_generation(v, config, include_internal=True)
            preflight_seconds=time.perf_counter()-preflight_started
            print(f"[GEN RUN] preflight end elapsed={preflight_seconds:.3f}s valid={check.get('valid')} errors={len(check.get('errors',[]))}", flush=True)
            if not check.get('valid'):
                check.pop('_requirements', None)
                print(f"[GEN RUN] total elapsed={time.perf_counter()-request_started:.3f}s status=400 stage=endpoint_preflight", flush=True)
                return Response(check, 400)
            validated_requirements = check.pop('_requirements')
            print('[GEN RUN] fingerprint start',flush=True)
            fingerprint_started=time.perf_counter()
            source_fp=fingerprint(v)
            fingerprint_seconds=time.perf_counter()-fingerprint_started
            print(f'[GEN RUN] fingerprint end elapsed={fingerprint_seconds:.3f}s',flush=True)
            from scheduling.provenance import build_input_snapshot,SNAPSHOT_VERSION
            snapshot,input_fp=build_input_snapshot(v,config,source_fp)
            run=GenerationRun.objects.create(timetable=v.timetable,source_version=v,created_by=request.user,mode=config.get('mode','FILL_GAPS'),input_config=config,input_snapshot=snapshot,input_snapshot_version=SNAPSHOT_VERSION,input_fingerprint=input_fp,source_fingerprint=source_fp);record('GENERATION_STARTED',request.user,timetable=v.timetable,version=v,entity_type='GenerationRun',entity_id=run.id)
            print(f'[GEN RUN] requirements ready count={len(validated_requirements)} elapsed={preflight_seconds:.3f}s (loaded by preflight)',flush=True)
            generated_run=run_generation(run,validated_requirements=validated_requirements)
            print('[GEN RUN] response serialization start',flush=True)
            serialization_started=time.perf_counter()
            response_data=GenerationRunSerializer(generated_run).data
            serialization_seconds=time.perf_counter()-serialization_started
            request_total_seconds=time.perf_counter()-request_started
            generated_run.statistics={**generated_run.statistics,'request_timings':{'source_version_load_seconds':source_load_seconds,'preflight_seconds':preflight_seconds,'fingerprint_seconds':fingerprint_seconds,'result_serialization_seconds':serialization_seconds,'request_total_seconds':request_total_seconds},'generation_request_db_query_count':query_counter.count+1}
            generated_run.save(update_fields=['statistics'])
            response_data=GenerationRunSerializer(generated_run).data
        print(f"[GEN RUN] serialization end elapsed={serialization_seconds:.3f}s db_queries={query_counter.count} total_elapsed={time.perf_counter()-request_started:.3f}s",flush=True)
        print(f"[GEN RUN] request end total_elapsed={time.perf_counter()-request_started:.3f}s status=201 solver_status={generated_run.solver_status}",flush=True)
        return Response(response_data,status=201)
class GenerationRunDetail(APIView):
    permission_classes=[IsAuthenticated]
    @extend_schema(responses=GenerationRunSerializer)
    def get(self,request,run_id):return Response(GenerationRunSerializer(GenerationRun.objects.get(pk=run_id)).data)
class GenerationApplyValidation(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(request=None,responses={200: serializers.DictField()})
    def post(self,request,run_id):
        from django.shortcuts import get_object_or_404
        run=get_object_or_404(GenerationRun.objects.select_related('source_version','timetable'),pk=run_id)
        return Response(validate_generation_apply(run),status=200)
class GenerationRunRevalidation(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(request=None,responses={200: serializers.DictField()})
    def post(self,request,run_id):
        from django.shortcuts import get_object_or_404
        from django.utils import timezone
        run=get_object_or_404(GenerationRun.objects.select_related('source_version','timetable'),pk=run_id)
        validation=validate_generation_apply(run)
        run.current_validation={'valid':validation['valid'],'blocker_count':validation['blocking_error_count'],'validated_at':timezone.now().isoformat(),'errors':validation['errors'],'source_fingerprint_matches':validation['source_fingerprint_matches'],'provenance_status':validation['provenance_status'],'input_fingerprint_matches':validation['input_fingerprint_matches']}
        run.save(update_fields=['current_validation'])
        return Response({**validation,'current_validation':run.current_validation},status=200)
class GenerationApply(APIView):
    permission_classes=[GenerationPermission]
    @extend_schema(request=None,responses={200: GenerationApplyResponseSerializer, 409: ErrorResponseSerializer})
    def post(self,request,run_id):
        try:new=apply_generation(GenerationRun.objects.get(pk=run_id));return Response({'applied_version':str(new.id),'version_no':new.version_no})
        except GenerationApplyError as e:
            conflicts=e.conflicts or []
            blocking_count=len(conflicts)
            run=GenerationRun.objects.filter(pk=run_id).first()
            if run:
                run.apply_validation={'valid':False,'blocker_count':blocking_count,'validated_at':__import__('django.utils.timezone',fromlist=['now']).now().isoformat(),'errors':conflicts}
                run.save(update_fields=['apply_validation'])
            return Response({'code':e.code,'message':e.message,'conflicts':conflicts,'errors':conflicts,'error_count':blocking_count,'blocking_error_count':blocking_count,'warning_count':0},409)
