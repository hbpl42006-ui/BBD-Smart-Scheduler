import uuid
from django.db import models
from accounts.models import User
class AuditEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    event_type=models.CharField(max_length=50)
    actor=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='audit_events')
    timetable_id=models.UUIDField(null=True,blank=True); version_id=models.UUIDField(null=True,blank=True)
    entity_type=models.CharField(max_length=80); entity_id=models.UUIDField(null=True,blank=True)
    old_data=models.JSONField(default=dict,blank=True); new_data=models.JSONField(default=dict,blank=True); metadata=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']; indexes=[models.Index(fields=['event_type','created_at']),models.Index(fields=['version_id','created_at'])]
