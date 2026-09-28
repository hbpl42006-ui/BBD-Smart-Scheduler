from rest_framework import serializers
from scheduling.models import GenerationRun
class GenerationConfigSerializer(serializers.Serializer):
    mode=serializers.ChoiceField(choices=['FILL_GAPS','REBUILD_UNLOCKED'],default='FILL_GAPS');section_ids=serializers.ListField(child=serializers.UUIDField(),required=False);offering_rules=serializers.ListField(child=serializers.JSONField(),required=False);max_solve_seconds=serializers.IntegerField(min_value=1,max_value=60,required=False);random_seed=serializers.IntegerField(required=False);soft_constraints=serializers.JSONField(required=False)
class ErrorDetailSerializer(serializers.Serializer):
    code=serializers.CharField();message=serializers.CharField()
class ErrorResponseSerializer(serializers.Serializer):
    code=serializers.CharField();message=serializers.CharField();error_count=serializers.IntegerField(required=False);warning_count=serializers.IntegerField(required=False);conflicts=ErrorDetailSerializer(many=True,required=False)
class GenerationPreflightResponseSerializer(serializers.Serializer):
    valid=serializers.BooleanField()
    errors=ErrorDetailSerializer(many=True,required=False)
    warnings=ErrorDetailSerializer(many=True,required=False)
class GenerationApplySerializer(serializers.Serializer):
    pass
class GenerationApplyResponseSerializer(serializers.Serializer):
    applied_version=serializers.UUIDField()
    version_no=serializers.IntegerField()
class GenerationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model=GenerationRun; fields='__all__'; read_only_fields=('id','status','solver_status','result','diagnostics','statistics','objective_score','source_fingerprint','created_by','created_at','started_at','completed_at','applied_at','applied_version')
