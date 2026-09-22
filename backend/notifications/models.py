import uuid
from django.conf import settings
from django.db import models

class NotificationPreference(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='notification_preferences')
    in_app_enabled=models.BooleanField(default=True)
    email_enabled=models.BooleanField(default=True)
    whatsapp_enabled=models.BooleanField(default=True)
    whatsapp_opt_in=models.BooleanField(default=False)
    whatsapp_opted_in_at=models.DateTimeField(null=True,blank=True)
    timetable_updates=models.BooleanField(default=True)
    publishing_updates=models.BooleanField(default=True)
    def __str__(self): return f'{self.user.email} notification preferences'

class Notification(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='notifications')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='issued_notifications')
    event_type=models.CharField(max_length=80)
    title=models.CharField(max_length=255)
    message=models.TextField()
    action_url=models.CharField(max_length=255,blank=True)
    metadata=models.JSONField(default=dict,blank=True)
    is_read=models.BooleanField(default=False)
    read_at=models.DateTimeField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class NotificationDelivery(models.Model):
    class Channel(models.TextChoices): EMAIL='EMAIL','Email'; WHATSAPP='WHATSAPP','WhatsApp'
    class Status(models.TextChoices): PENDING='PENDING','Pending'; SENDING='SENDING','Sending'; SENT='SENT','Sent'; DELIVERED='DELIVERED','Delivered'; READ='READ','Read'; FAILED='FAILED','Failed'; SKIPPED='SKIPPED','Skipped'
    notification=models.ForeignKey(Notification,on_delete=models.CASCADE,related_name='deliveries')
    channel=models.CharField(max_length=20,choices=Channel.choices)
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.PENDING)
    recipient_address=models.EmailField()
    provider_message_id=models.CharField(max_length=255,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    last_error=models.TextField(blank=True)
    sent_at=models.DateTimeField(null=True,blank=True)
    delivered_at=models.DateTimeField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['notification','channel'],name='unique_notification_channel')]

# Create your models here.
