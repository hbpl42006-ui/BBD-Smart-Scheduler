from rest_framework import serializers
from scheduling.models import GenerationRun
class GenerationConfigSerializer(serializers.Serializer):
    mode=serializers.ChoiceField(choices=['FILL_GAPS','REBUILD_UNLOCKED'],default='FILL_GAPS');section_ids=serializers.ListField(child=serializers.UUIDField(),required=False);offering_rules=serializers.ListField(child=serializers.JSONField(),required=False);max_solve_seconds=serializers.IntegerField(min_value=30,max_value=600,default=120,required=False);random_seed=serializers.IntegerField(required=False);soft_constraints=serializers.JSONField(required=False);allow_remainder_period=serializers.BooleanField(required=False,default=False)
class ErrorDetailSerializer(serializers.Serializer):
    code=serializers.CharField();message=serializers.CharField()
class ErrorResponseSerializer(serializers.Serializer):
    code=serializers.CharField();message=serializers.CharField();error_count=serializers.IntegerField(required=False);blocking_error_count=serializers.IntegerField(required=False);warning_count=serializers.IntegerField(required=False);conflicts=ErrorDetailSerializer(many=True,required=False);errors=ErrorDetailSerializer(many=True,required=False)
class GenerationPreflightResponseSerializer(serializers.Serializer):
    valid=serializers.BooleanField()
    errors=ErrorDetailSerializer(many=True,required=False)
    warnings=ErrorDetailSerializer(many=True,required=False)
    mode=serializers.ChoiceField(choices=['FILL_GAPS','REBUILD_UNLOCKED'],required=False)
    statistics=serializers.DictField(required=False)
class GenerationApplySerializer(serializers.Serializer):
    pass
class GenerationApplyResponseSerializer(serializers.Serializer):
    applied_version=serializers.UUIDField()
    version_no=serializers.IntegerField()
class GenerationRunSerializer(serializers.ModelSerializer):
    generation_input_fingerprint=serializers.SerializerMethodField()
    current_input_fingerprint=serializers.SerializerMethodField()
    input_fingerprint_matches=serializers.SerializerMethodField()
    provenance_status=serializers.SerializerMethodField()
    def _provenance(self,obj):
        cache=getattr(self,'_provenance_cache',{})
        if str(obj.pk) in cache:return cache[str(obj.pk)]
        from scheduling.provenance import provenance_for_run
        cache[str(obj.pk)]=provenance_for_run(obj);self._provenance_cache=cache
        return cache[str(obj.pk)]
    def get_generation_input_fingerprint(self,obj):return self._provenance(obj)['generation_input_fingerprint']
    def get_current_input_fingerprint(self,obj):return self._provenance(obj)['current_input_fingerprint']
    def get_input_fingerprint_matches(self,obj):return self._provenance(obj)['input_fingerprint_matches']
    def get_provenance_status(self,obj):return self._provenance(obj)['provenance_status']
    class Meta:
        model=GenerationRun; fields='__all__'; read_only_fields=('id','status','solver_status','result','diagnostics','statistics','objective_score','source_fingerprint','input_snapshot','input_snapshot_version','input_fingerprint','generation_validation','current_validation','apply_validation','created_by','created_at','started_at','completed_at','applied_at','applied_version')
