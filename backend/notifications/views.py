from django.shortcuts import render

# Create your views here.
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Notification
from .models import NotificationPreference,NotificationDelivery
from django.conf import settings
import json,hmac,hashlib
from .services import sanitize_provider_error

def serialize(n): return {'id':str(n.id),'event_type':n.event_type,'title':n.title,'message':n.message,'action_url':n.action_url,'metadata':n.metadata,'is_read':n.is_read,'created_at':n.created_at}
class NotificationList(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        qs=Notification.objects.filter(recipient=request.user)
        if request.query_params.get('unread') in ('1','true','True'): qs=qs.filter(is_read=False)
        page=max(int(request.query_params.get('page',1)),1); size=20; total=qs.count(); rows=qs[(page-1)*size:page*size]
        return Response({'results':[serialize(n) for n in rows],'count':total,'page':page,'page_size':size})
class NotificationUnreadCount(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request): return Response({'count':Notification.objects.filter(recipient=request.user,is_read=False).count()})
class NotificationRead(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,notification_id):
        n=Notification.objects.filter(pk=notification_id,recipient=request.user).first()
        if not n:return Response({'detail':'Notification not found.'},404)
        n.is_read=True;n.read_at=timezone.now();n.save(update_fields=['is_read','read_at']);return Response(serialize(n))
class NotificationMarkAllRead(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request):
        Notification.objects.filter(recipient=request.user,is_read=False).update(is_read=True,read_at=timezone.now());return Response({'updated':True})
def pref_data(user,pref): return {'in_app_enabled':pref.in_app_enabled,'email_enabled':pref.email_enabled,'whatsapp_enabled':pref.whatsapp_enabled,'whatsapp_opt_in':pref.whatsapp_opt_in,'whatsapp_number':getattr(user,'whatsapp_number',''),'whatsapp_available':getattr(settings,'WHATSAPP_ENABLED',False)}
class NotificationPreferences(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request): return Response(pref_data(request.user,NotificationPreference.objects.get_or_create(user=request.user)[0]))
    def patch(self,request):
        pref=NotificationPreference.objects.get_or_create(user=request.user)[0]
        for field in ('in_app_enabled','email_enabled','whatsapp_enabled','whatsapp_opt_in'):
            if field in request.data: setattr(pref,field,bool(request.data[field]))
        if pref.whatsapp_opt_in and not getattr(request.user,'whatsapp_number',''): pref.whatsapp_opt_in=False
        pref.save();return Response(pref_data(request.user,pref))
class WhatsAppWebhook(APIView):
    authentication_classes=[]; permission_classes=[]
    def get(self,request):
        if request.query_params.get('hub.verify_token')!=getattr(settings,'WHATSAPP_WEBHOOK_VERIFY_TOKEN',''): return Response({'detail':'Invalid verification token.'},403)
        return Response(request.query_params.get('hub.challenge',''))
    def post(self,request):
        secret=getattr(settings,'WHATSAPP_APP_SECRET','');signature=request.headers.get('X-Hub-Signature-256','')
        expected='sha256='+hmac.new(secret.encode(),request.body,hashlib.sha256).hexdigest() if secret else ''
        if not secret or not signature or not hmac.compare_digest(signature,expected): return Response({'detail':'Invalid webhook signature.'},403)
        for entry in request.data.get('entry',[]):
            for change in entry.get('changes',[]):
                for status in change.get('value',{}).get('statuses',[]):
                    provider_id=status.get('id'); delivery=NotificationDelivery.objects.filter(provider_message_id=provider_id,channel='WHATSAPP').first()
                    if not delivery: continue
                    mapping={'sent':'SENT','delivered':'DELIVERED','read':'READ','failed':'FAILED'};new=mapping.get(status.get('status'))
                    priority={'PENDING':0,'SENDING':1,'SENT':2,'DELIVERED':3,'READ':4,'FAILED':1,'SKIPPED':5}
                    if new and ((new=='FAILED' and delivery.status not in ('READ','SKIPPED')) or priority.get(new,0)>=priority.get(delivery.status,0)):
                        delivery.status=new
                        fields=['status','updated_at']
                        if new=='DELIVERED': delivery.delivered_at=timezone.now();fields.append('delivered_at')
                        if new=='FAILED':
                            errors=status.get('errors') or []
                            delivery.last_error=sanitize_provider_error(json.dumps(errors,ensure_ascii=True) if errors else 'WhatsApp delivery failed.')
                            fields.append('last_error')
                        delivery.save(update_fields=fields)
        return Response({'received':True})
