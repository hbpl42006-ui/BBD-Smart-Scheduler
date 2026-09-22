from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Notification,NotificationDelivery,NotificationPreference
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display=('recipient','event_type','title','is_read','created_at'); list_filter=('event_type','is_read'); search_fields=('recipient__email','title','message')
@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display=('channel','status','recipient_address','attempts','provider_message_id','sent_at','created_at'); list_filter=('channel','status'); readonly_fields=('notification','channel','recipient_address','attempts','provider_message_id','last_error','sent_at','delivered_at','created_at','updated_at')
@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display=('user','in_app_enabled','email_enabled')
