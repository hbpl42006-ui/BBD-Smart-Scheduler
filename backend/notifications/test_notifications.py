import pytest
import hashlib,hmac,json
import urllib.error
from unittest.mock import patch
from django.core.management import call_command
from django.core import mail
from rest_framework.test import APIClient
from accounts.models import User,Role
from .models import Notification,NotificationDelivery,NotificationPreference
from .services import notify
from .models import NotificationDelivery

@pytest.fixture
def users(db):
    first=User.objects.create_user('first-notify@test.local','Pass12345!',role=Role.FACULTY,first_name='Aarav',last_name='Sharma')
    other=User.objects.create_user('other-notify@test.local','Pass12345!',role=Role.FACULTY)
    NotificationPreference.objects.update_or_create(user=first,defaults={'email_enabled':False})
    return first,other
def auth(user):
    client=APIClient();client.force_authenticate(user);return client
def test_notification_creation_and_recipient_isolation(users):
    first,other=users; notify(event_type='TIMETABLE_PUBLISHED',recipients=[first],title='Published',message='Your timetable is live.')
    response=auth(first).get('/api/notifications/'); assert response.status_code==200 and response.data['count']==1
    assert auth(other).get('/api/notifications/').data['count']==0
def test_unread_count_mark_read_and_mark_all(users):
    first,_=users; rows=notify(event_type='TIMETABLE_ENTRY_UPDATED',recipients=[first],title='Updated',message='Room changed.')
    client=auth(first); assert client.get('/api/notifications/unread-count/').data['count']==1
    assert client.post(f'/api/notifications/{rows[0].pk}/read/').status_code==200
    assert client.get('/api/notifications/unread-count/').data['count']==0
    notify(event_type='TIMETABLE_PUBLISHED',recipients=[first],title='Published',message='Live.')
    assert client.post('/api/notifications/mark-all-read/').status_code==200
    assert client.get('/api/notifications/unread-count/').data['count']==0
def test_email_disabled_prevents_delivery(users):
    first,_=users; notify(event_type='TIMETABLE_PUBLISHED',recipients=[first],title='Published',message='Live.')
    assert not NotificationDelivery.objects.filter(notification__recipient=first).exists()

def _email_delivery(user,**over):
    n=Notification.objects.create(recipient=user,event_type='TIMETABLE_UPDATED',title='Updated',message='Room changed.',action_url='/my-timetable')
    return NotificationDelivery.objects.create(notification=n,channel='EMAIL',recipient_address=user.email,**over)

def test_email_delivery_uses_django_backend_with_text_and_html(users,settings):
    first,_=users;settings.EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend';delivery=_email_delivery(first)
    from .services import send_delivery
    send_delivery(delivery);delivery.refresh_from_db()
    assert delivery.status=='SENT' and delivery.attempts==1 and delivery.last_error=='' and delivery.sent_at is not None
    assert len(mail.outbox)==1 and mail.outbox[0].body.startswith('Updated')
    assert any(attachment[1]=='text/html' for attachment in mail.outbox[0].alternatives)

def test_email_delivery_failure_is_recorded_without_connect_error(users,settings):
    first,_=users;settings.EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend';settings.EMAIL_HOST='invalid.invalid';settings.EMAIL_PORT=1;settings.EMAIL_USE_TLS=False;delivery=_email_delivery(first)
    from .services import send_delivery
    send_delivery(delivery);delivery.refresh_from_db()
    assert delivery.status=='FAILED' and delivery.attempts==1 and 'connect() first' not in delivery.last_error

def test_retry_failed_email_sends_existing_delivery_without_duplicate(users,settings):
    first,_=users;settings.EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend';delivery=_email_delivery(first,status='FAILED',attempts=1)
    call_command('retry_notification_emails','--channel','email');delivery.refresh_from_db()
    assert delivery.status=='SENT' and delivery.attempts==2 and len(mail.outbox)==1
    assert NotificationDelivery.objects.filter(notification=delivery.notification,channel='EMAIL').count()==1
def test_failed_email_delivery_is_recorded_without_affecting_notification(users,settings):
    first,_=users; NotificationPreference.objects.filter(user=first).update(email_enabled=True)
    settings.EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend';settings.EMAIL_HOST='invalid.invalid';settings.EMAIL_PORT=1
    created=notify(event_type='TIMETABLE_ENTRY_UPDATED',recipients=[first],title='Updated',message='Room changed.')
    assert Notification.objects.filter(pk=created[0].pk).exists()

def test_whatsapp_requires_explicit_opt_in_and_number(users,settings):
    first,_=users; first.whatsapp_number='+919876543210';first.save(update_fields=['whatsapp_number'])
    pref=NotificationPreference.objects.get(user=first);pref.email_enabled=False;pref.whatsapp_enabled=True;pref.whatsapp_opt_in=True;pref.save()
    settings.WHATSAPP_ENABLED=False
    created=notify(event_type='TIMETABLE_PUBLISHED',recipients=[first],title='Published',message='Live.')
    delivery=NotificationDelivery.objects.get(notification=created[0],channel='WHATSAPP');assert delivery.status=='SKIPPED'
    pref.whatsapp_opt_in=False;pref.save(update_fields=['whatsapp_opt_in'])
    created=notify(event_type='TIMETABLE_PUBLISHED',recipients=[first],title='Published',message='Live.')
    assert NotificationDelivery.objects.filter(notification=created[0],channel='WHATSAPP').exists() is False

def test_preferences_api_isolated_and_safe(users):
    first,other=users; client=auth(first)
    response=client.get('/api/notifications/preferences/');assert response.status_code==200 and 'whatsapp_available' in response.data and 'WHATSAPP_ACCESS_TOKEN' not in response.data
    assert client.patch('/api/notifications/preferences/',{'email_enabled':False,'whatsapp_opt_in':True},format='json').status_code==200
    assert client.get('/api/notifications/preferences/').data['email_enabled'] is False
    assert auth(other).get('/api/notifications/preferences/').status_code==200

def _delivery(user,**over):
    n=Notification.objects.create(recipient=user,event_type='TIMETABLE_UPDATED',title='Updated',message='Changed')
    values={'provider_message_id':'wamid.test',**over}
    return NotificationDelivery.objects.create(notification=n,channel='WHATSAPP',recipient_address='+919876543210',**values)
def test_webhook_verification_and_status_transitions(users,settings):
    first,_=users;settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN='verify';settings.WHATSAPP_APP_SECRET='secret';delivery=_delivery(first);client=APIClient()
    assert client.get('/api/notifications/whatsapp/webhook/',{'hub.verify_token':'verify','hub.challenge':'abc'}).status_code==200
    assert client.get('/api/notifications/whatsapp/webhook/',{'hub.verify_token':'bad','hub.challenge':'abc'}).status_code==403
    body=json.dumps({'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.test','status':'delivered'}]}}]}]}).encode();sig='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest()
    assert client.post('/api/notifications/whatsapp/webhook/',body,content_type='application/json',HTTP_X_HUB_SIGNATURE_256=sig).status_code==200
    delivery.refresh_from_db();assert delivery.status=='DELIVERED'
    body=json.dumps({'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.test','status':'sent'}]}}]}]}).encode();sig='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest();client.post('/api/notifications/whatsapp/webhook/',body,content_type='application/json',HTTP_X_HUB_SIGNATURE_256=sig);delivery.refresh_from_db();assert delivery.status=='DELIVERED'
def test_webhook_rejects_missing_or_invalid_signature(users,settings):
    settings.WHATSAPP_APP_SECRET='secret';client=APIClient();body=b'{"entry":[]}'
    assert client.post('/api/notifications/whatsapp/webhook/',body,content_type='application/json').status_code==403
    assert client.post('/api/notifications/whatsapp/webhook/',body,content_type='application/json',HTTP_X_HUB_SIGNATURE_256='sha256=bad').status_code==403

class _MetaResponse:
    def __init__(self,body=b'{"messages":[{"id":"wamid.accepted"}]}'): self.body=body
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return self.body
def test_meta_provider_request_and_provider_id(users,settings):
    first,_=users;first.whatsapp_number='+919876543210';first.save(update_fields=['whatsapp_number']);pref=NotificationPreference.objects.get(user=first);pref.email_enabled=False;pref.whatsapp_enabled=True;pref.whatsapp_opt_in=True;pref.save();settings.WHATSAPP_ENABLED=True;settings.WHATSAPP_ACCESS_TOKEN='secret-token';settings.WHATSAPP_PHONE_NUMBER_ID='phone-id';settings.WHATSAPP_TEMPLATE_TIMETABLE_UPDATED='timetable_updated'
    created=notify(event_type='TIMETABLE_UPDATED',recipients=[first],title='Updated',message='Room changed.');delivery=NotificationDelivery.objects.get(notification=created[0],channel='WHATSAPP')
    with patch('urllib.request.urlopen',return_value=_MetaResponse()) as mocked: from .services import send_whatsapp;send_whatsapp(delivery)
    request=mocked.call_args.args[0];assert request.full_url.endswith('/phone-id/messages');assert request.get_header('Authorization')=='Bearer secret-token';payload=json.loads(request.data);assert payload['to']=='+919876543210' and payload['template']['name']=='timetable_updated' and payload['template']['language']['code']=='en';delivery.refresh_from_db();assert delivery.status=='SENT' and delivery.provider_message_id=='wamid.accepted' and mocked.call_count==1;assert 'secret-token' not in delivery.last_error
def test_meta_provider_failure_is_sanitized_and_retryable(users,settings):
    first,_=users;first.whatsapp_number='+919876543210';first.save(update_fields=['whatsapp_number']);pref=NotificationPreference.objects.get(user=first);pref.email_enabled=False;pref.whatsapp_opt_in=True;pref.save();settings.WHATSAPP_ENABLED=True;settings.WHATSAPP_ACCESS_TOKEN='secret-token';settings.WHATSAPP_PHONE_NUMBER_ID='phone-id';settings.WHATSAPP_TEMPLATE_TIMETABLE_UPDATED='updated';created=notify(event_type='TIMETABLE_UPDATED',recipients=[first],title='Updated',message='Changed');delivery=NotificationDelivery.objects.get(notification=created[0],channel='WHATSAPP')
    with patch('urllib.request.urlopen',side_effect=TimeoutError('provider timeout secret-token')): from .services import send_whatsapp;send_whatsapp(delivery)
    delivery.refresh_from_db();assert delivery.status=='FAILED' and delivery.attempts==1 and 'secret-token' not in delivery.last_error
@pytest.mark.parametrize('code',[400,401,403,429,500,502,503])
def test_meta_http_errors_fail_delivery_without_leaking_token(users,settings,code):
    first,_=users;first.whatsapp_number='+919876543210';first.save(update_fields=['whatsapp_number']);pref=NotificationPreference.objects.get(user=first);pref.email_enabled=False;pref.whatsapp_opt_in=True;pref.save();settings.WHATSAPP_ENABLED=True;settings.WHATSAPP_ACCESS_TOKEN='secret-token';settings.WHATSAPP_PHONE_NUMBER_ID='phone-id';settings.WHATSAPP_TEMPLATE_TIMETABLE_UPDATED='updated';created=notify(event_type='TIMETABLE_UPDATED',recipients=[first],title='Updated',message='Changed');delivery=NotificationDelivery.objects.get(notification=created[0],channel='WHATSAPP')
    with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://graph.facebook.com',code,'bad secret-token',{},None)):
        from .services import send_whatsapp;send_whatsapp(delivery)
    delivery.refresh_from_db();assert delivery.status=='FAILED' and delivery.attempts==1 and 'secret-token' not in delivery.last_error

def test_meta_malformed_success_is_failed_without_inventing_provider_id(users,settings):
    first,_=users;settings.WHATSAPP_ENABLED=True;settings.WHATSAPP_ACCESS_TOKEN='secret-token';settings.WHATSAPP_PHONE_NUMBER_ID='phone-id';settings.WHATSAPP_TEMPLATE_TIMETABLE_UPDATED='updated'
    delivery=_delivery(first,provider_message_id='')
    with patch('urllib.request.urlopen',return_value=_MetaResponse(b'{"messages":[]}')):
        from .services import send_whatsapp;send_whatsapp(delivery)
    delivery.refresh_from_db();assert delivery.status=='FAILED' and delivery.attempts==1 and delivery.provider_message_id=='' and 'provider message ID' in delivery.last_error

def test_meta_sensitive_bearer_error_is_sanitized(users,settings):
    first,_=users;settings.WHATSAPP_ENABLED=True;settings.WHATSAPP_ACCESS_TOKEN='secret-token';settings.WHATSAPP_PHONE_NUMBER_ID='phone-id';settings.WHATSAPP_TEMPLATE_TIMETABLE_UPDATED='updated'
    delivery=_delivery(first,provider_message_id='')
    with patch('urllib.request.urlopen',side_effect=RuntimeError('Authorization: Bearer abc.def secret-token')):
        from .services import send_whatsapp;send_whatsapp(delivery)
    delivery.refresh_from_db();assert delivery.status=='FAILED' and 'abc.def' not in delivery.last_error and 'secret-token' not in delivery.last_error

def test_retry_failed_whatsapp_then_success_does_not_duplicate_delivery(users):
    first,_=users;delivery=_delivery(first,status='FAILED',attempts=1,provider_message_id='')
    with patch('notifications.management.commands.retry_notification_emails.send_whatsapp') as sender:
        sender.side_effect=lambda item: NotificationDelivery.objects.filter(pk=item.pk).update(status='SENT',attempts=item.attempts+1,provider_message_id='wamid.retry')
        call_command('retry_notification_emails')
    delivery.refresh_from_db();assert sender.call_count==1 and delivery.status=='SENT' and delivery.attempts==2 and delivery.provider_message_id=='wamid.retry'
    assert NotificationDelivery.objects.filter(notification=delivery.notification,channel='WHATSAPP').count()==1

def test_retry_failed_whatsapp_can_fail_twice_then_succeed(users):
    first,_=users;delivery=_delivery(first,status='FAILED',attempts=0,provider_message_id='')
    outcomes=[('FAILED','first failure',''),('FAILED','second failure',''),('SENT','', 'wamid.recovered')]
    def send(item):
        status,error,provider_id=outcomes.pop(0);NotificationDelivery.objects.filter(pk=item.pk).update(status=status,attempts=item.attempts+1,last_error=error,provider_message_id=provider_id)
    with patch('notifications.management.commands.retry_notification_emails.send_whatsapp',side_effect=send) as sender:
        call_command('retry_notification_emails');call_command('retry_notification_emails');call_command('retry_notification_emails')
    delivery.refresh_from_db();assert sender.call_count==3 and delivery.attempts==3 and delivery.status=='SENT' and delivery.provider_message_id=='wamid.recovered'
    assert NotificationDelivery.objects.filter(notification=delivery.notification,channel='WHATSAPP').count()==1

def _signed_post(client,settings,payload):
    settings.WHATSAPP_APP_SECRET='secret';body=json.dumps(payload).encode();signature='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest()
    return client.post('/api/notifications/whatsapp/webhook/',body,content_type='application/json',HTTP_X_HUB_SIGNATURE_256=signature)

@pytest.mark.parametrize(('provider_status','expected'),[('sent','SENT'),('delivered','DELIVERED'),('read','READ'),('failed','FAILED')])
def test_webhook_status_and_duplicate_events_are_idempotent(users,settings,provider_status,expected):
    first,_=users;delivery=_delivery(first,status='PENDING');client=APIClient();status={'id':'wamid.test','status':provider_status}
    if provider_status=='failed': status['errors']=[{'code':131000,'title':'Delivery failed','message':'safe detail'}]
    payload={'entry':[{'changes':[{'value':{'statuses':[status]}}]}]}
    assert _signed_post(client,settings,payload).status_code==200;assert _signed_post(client,settings,payload).status_code==200
    delivery.refresh_from_db();assert delivery.status==expected
    if expected=='FAILED': assert 'Delivery failed' in delivery.last_error

@pytest.mark.parametrize(('sequence','expected'),[(('sent','delivered','read'),'READ'),(('read','sent'),'READ'),(('read','delivered'),'READ'),(('delivered','sent'),'DELIVERED')])
def test_webhook_statuses_are_monotonic(users,settings,sequence,expected):
    first,_=users;delivery=_delivery(first,status='PENDING');client=APIClient()
    for status in sequence: assert _signed_post(client,settings,{'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.test','status':status}]}}]}]}).status_code==200
    delivery.refresh_from_db();assert delivery.status==expected

def test_webhook_multiple_unknown_unrelated_and_malformed_events_are_safe(users,settings):
    first,other=users;one=_delivery(first,provider_message_id='wamid.one');two=_delivery(other,provider_message_id='wamid.two');client=APIClient()
    payload={'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.one','status':'delivered'},{'id':'unknown','status':'read'},{'id':'wamid.two','status':'read'}]}},{'value':{'messages':[{'type':'text'}]}}]}]}
    assert _signed_post(client,settings,payload).status_code==200
    for delivery,expected in ((one,'DELIVERED'),(two,'READ')): delivery.refresh_from_db();assert delivery.status==expected
    assert _signed_post(client,settings,{}).status_code==200

def test_failed_webhook_error_is_sanitized(users,settings):
    first,_=users;delivery=_delivery(first,status='SENT');client=APIClient();settings.WHATSAPP_ACCESS_TOKEN='secret-token'
    status={'id':'wamid.test','status':'failed','errors':[{'message':'Authorization Bearer abc.def secret-token'}]}
    assert _signed_post(client,settings,{'entry':[{'changes':[{'value':{'statuses':[status]}}]}]}).status_code==200
    delivery.refresh_from_db();assert delivery.status=='FAILED' and 'abc.def' not in delivery.last_error and 'secret-token' not in delivery.last_error
def test_whatsapp_terminal_and_skipped_deliveries_are_not_retried(users):
    first,_=users
    for status in ('SENT','DELIVERED','READ','SKIPPED'):
        delivery=_delivery(first,status=status,attempts=2)
    from django.core.management import call_command
    with patch('notifications.management.commands.retry_notification_emails.send_whatsapp') as mocked: call_command('retry_notification_emails')
    assert mocked.call_count==0
