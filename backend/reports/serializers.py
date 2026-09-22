from rest_framework import serializers

class ReportRowSerializer(serializers.Serializer):
    def to_representation(self, instance): return instance
