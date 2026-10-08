from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.defaultfilters import escape
from django.utils import timezone
from .models import Notification,NotificationDelivery,NotificationPreference
import json,re,urllib.request,urllib.error

def sanitize_provider_error(value):
    text=str(value or '')
    token=getattr(settings,'WHATSAPP_ACCESS_TOKEN','')
    if token: text=text.replace(token,'[redacted]')
    text=re.sub(r'(?i)bearer\s+[a-z0-9._-]+','Bearer [redacted]',text)
    return text[:500]

def _preference(user): return NotificationPreference.objects.get_or_create(user=user)[0]
def send_delivery(delivery):
    delivery.attempts+=1
    try:
        n=delivery.notification; url=f"{getattr(settings,'FRONTEND_URL','http://localhost:3000')}{n.action_url}"
        text=f'{n.title}\n\n{n.message}\n\nView in BBD Smart Scheduler: {url}'
        html=f'<h2>Babu Banarasi Das University</h2><h3>{escape(n.title)}</h3><p>{escape(n.message).replace(chr(10),"<br>")}</p><p><a href="{escape(url)}">View in BBD Smart Scheduler</a></p><p>This is an automated notification from BBD Smart Scheduler.</p>'
        msg=EmailMultiAlternatives(n.title,text,settings.DEFAULT_FROM_EMAIL,[delivery.recipient_address]);msg.attach_alternative(html,'text/html')
        # Open a fresh Django-managed backend connection for every delivery.
        # This avoids reusing a closed SMTP connection between callbacks/retries.
        with get_connection(fail_silently=False) as connection:
            connection.send_messages([msg])
        delivery.status='SENT';delivery.sent_at=timezone.now();delivery.last_error=''
    except Exception as exc:
        delivery.status='FAILED';delivery.last_error=str(exc)[:500]
    delivery.save(update_fields=['attempts','status','sent_at','last_error','updated_at']);return delivery
def _phone(value):
    value=re.sub(r'[^0-9+]','',value or '')
    return value if re.fullmatch(r'\+[1-9][0-9]{7,14}',value) else ''
def send_whatsapp(delivery):
    delivery.attempts+=1; n=delivery.notification
    if not getattr(settings,'WHATSAPP_ENABLED',False): delivery.status='SKIPPED';delivery.last_error='WhatsApp is disabled.'
    elif not getattr(settings,'WHATSAPP_ACCESS_TOKEN','') or not getattr(settings,'WHATSAPP_PHONE_NUMBER_ID',''): delivery.status='SKIPPED';delivery.last_error='WhatsApp provider is not configured.'
    elif not getattr(settings,'WHATSAPP_TEMPLATE_TIMETABLE_PUBLISHED','') and n.event_type=='TIMETABLE_PUBLISHED': delivery.status='SKIPPED';delivery.last_error='WhatsApp template not configured.'
    else:
        try:
            version=getattr(settings,'WHATSAPP_GRAPH_API_VERSION','v20.0');endpoint=f"https://graph.facebook.com/{version}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages";template=getattr(settings,'WHATSAPP_TEMPLATE_TIMETABLE_UPDATED' if n.event_type!='TIMETABLE_PUBLISHED' else 'WHATSAPP_TEMPLATE_TIMETABLE_PUBLISHED','')
            body={'messaging_product':'whatsapp','to':delivery.recipient_address,'type':'template','template':{'name':template,'language':{'code':getattr(settings,'WHATSAPP_DEFAULT_LANGUAGE','en')}}}
            req=urllib.request.Request(endpoint,data=json.dumps(body).encode(),headers={'Authorization':f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}','Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(req,timeout=10) as response:
                data=json.loads(response.read().decode())
            provider_message_id=str((data.get('messages') or [{}])[0].get('id','')).strip()
            if not provider_message_id: raise ValueError('Meta response did not contain a provider message ID.')
            delivery.provider_message_id=provider_message_id;delivery.status='SENT';delivery.sent_at=timezone.now();delivery.last_error=''
        except Exception as exc:
            delivery.status='FAILED';delivery.last_error=sanitize_provider_error(exc)
    delivery.save(update_fields=['attempts','status','sent_at','last_error','provider_message_id','updated_at']);return delivery
def notify(*,event_type,recipients,title,message,action_url='/my-timetable',metadata=None,actor=None,email=True):
    created=[]
    for user in recipients:
        pref=_preference(user)
        if not pref.in_app_enabled and not (email and pref.email_enabled): continue
        n=Notification.objects.create(recipient=user,actor=actor,event_type=event_type,title=title,message=message,action_url=action_url,metadata=metadata or {})
        faculty_email = getattr(getattr(user, 'faculty_profile', None), 'email', None)
        recipient_email = faculty_email or (None if getattr(user, 'faculty_profile', None) else user.email)
        if email and pref.email_enabled and recipient_email:
            delivery=NotificationDelivery.objects.create(notification=n,channel='EMAIL',recipient_address=recipient_email)
            from django.db import transaction
            transaction.on_commit(lambda delivery=delivery: send_delivery(delivery))
        if pref.whatsapp_enabled and pref.whatsapp_opt_in:
            phone=_phone(getattr(user,'whatsapp_number',''))
            delivery=NotificationDelivery.objects.create(notification=n,channel='WHATSAPP',recipient_address=phone or '')
            from django.db import transaction
            if not phone: delivery.status='SKIPPED';delivery.last_error='No valid opted-in WhatsApp number.';delivery.save(update_fields=['status','last_error','updated_at'])
            elif not getattr(settings,'WHATSAPP_ENABLED',False): delivery.status='SKIPPED';delivery.last_error='WhatsApp is disabled.';delivery.save(update_fields=['status','last_error','updated_at'])
            else: transaction.on_commit(lambda delivery=delivery: send_whatsapp(delivery))
        created.append(n)
    return created

def notify_arrangement_event(arrangement, event, actor=None, old_substitute=None):
    """Create durable in-app/email notices without making delivery part of the write."""
    from faculty.arrangement_views import faculty_name
    entry=arrangement.schedule_entry
    date=arrangement.arrangement_date.strftime('%d %b %Y')
    details=(f'Date: {date}\nDay: {entry.get_weekday_display()}\nTime: {entry.start_slot.label}\n'
             f'Course: {entry.course_offering.course.code}\nCourse Name: {entry.course_offering.course.name}\n'
             f'Section: {entry.section}\nRoom: {entry.room.code if entry.room else "—"}\n'
             f'Original Faculty: {faculty_name(arrangement.absent_faculty)}')
    if event=='ASSIGNED':
        title='New Arrangement Class Assigned'; message=f'You have been assigned an arrangement class.\n\n{details}\n\nAfter completing the arranged class, upload the attendance sheet (JPG/PNG/WEBP/PDF) from My Arrangements.'; recipients=[arrangement.substitute_faculty.user] if arrangement.substitute_faculty.user_id else []
    elif event=='REASSIGNED':
        title='Arrangement Class Reassigned'; message=f'The arrangement class scheduled for {date}, {entry.start_slot.label}, {entry.course_offering.course.code} / {entry.section} has been reassigned and is no longer assigned to you.\n\n{details}'; recipients=[old_substitute.user] if old_substitute and old_substitute.user_id else []
    elif event=='CANCELLED':
        title='Arrangement Class Cancelled'; message=f'Your arrangement class has been cancelled.\n\n{details}'; recipients=[arrangement.substitute_faculty.user] if arrangement.substitute_faculty.user_id else []
    else: return []
    return notify(event_type=f'ARRANGEMENT_{event}',recipients=recipients,title=title,message=message,action_url='/faculty-arrangements',metadata={'arrangement_id':str(arrangement.pk)},actor=actor)

def notify_arrangement_after_commit(arrangement, event, actor=None, old_substitute=None):
    from django.db import transaction
    transaction.on_commit(lambda: notify_arrangement_event(arrangement, event, actor, old_substitute))

def notify_arrangement_attendance_after_commit(arrangement, evidence, actor=None):
    """Notify only the original faculty when assigned evidence is persisted."""
    from django.db import transaction
    arrangement_id=str(arrangement.pk); evidence_id=str(evidence.pk); actor_id=getattr(actor,'pk',None)
    def create_notice():
        from faculty.models import ArrangementAttendanceEvidence, FacultyArrangement
        from faculty.arrangement_views import faculty_name
        from accounts.models import User
        item=FacultyArrangement.objects.select_related(
            'absent_faculty__user','substitute_faculty__user','schedule_entry__start_slot',
            'schedule_entry__course_offering__course','schedule_entry__section',
        ).filter(pk=arrangement_id).first()
        uploaded=ArrangementAttendanceEvidence.objects.filter(pk=evidence_id,arrangement_id=arrangement_id).first()
        if not item or not uploaded or not item.absent_faculty.user_id:return
        original_user=item.absent_faculty.user
        if actor_id and str(original_user.pk)==str(actor_id):return
        entry=item.schedule_entry
        date=item.arrangement_date.strftime('%d %b %Y')
        title=f'Attendance uploaded for {entry.course_offering.course.code} - {entry.section}'
        message=(f'{faculty_name(item.substitute_faculty)} uploaded attendance for your class on '
                 f'{date}, {entry.start_slot.label}.\nCourse: {entry.course_offering.course.code} - '
                 f'{entry.course_offering.course.name}\nSection: {entry.section}\n'
                 f'File: {uploaded.original_filename}')
        recipient=User.objects.filter(pk=original_user.pk,is_active=True).first()
        if recipient:
            notify(event_type='ARRANGEMENT_ATTENDANCE_UPLOADED',recipients=[recipient],title=title,message=message,
                   action_url=f'/faculty-arrangements/attendance/{arrangement_id}/{evidence_id}',
                   metadata={'arrangement_id':arrangement_id,'evidence_id':evidence_id},actor=actor)
    transaction.on_commit(create_notice)

def _entry_snapshot(entry):
    faculty=tuple(sorted(str(x) for x in entry.faculty_assignments.values_list('faculty_id',flat=True)))
    return {'section':str(entry.section_id),'course_offering':str(entry.course_offering_id),'course_code':entry.course_offering.course.code,'course_name':entry.course_offering.course.name,'weekday':entry.weekday,'start_slot':str(entry.start_slot_id),'start_label':entry.start_slot.label,'block_length':entry.block_length,'room':str(entry.room_id) if entry.room_id else None,'room_label':entry.room.code if entry.room else '','faculty':faculty}
def publish_diff(old_version,new_version):
    old_entries=list(old_version.entries.select_related('course_offering__course','start_slot','room').prefetch_related('faculty_assignments')) if old_version else []
    new_entries=list(new_version.entries.select_related('course_offering__course','start_slot','room').prefetch_related('faculty_assignments'))
    # A section can contain multiple entries for the same course. Match the
    # nth occurrence of each section/course pair, rather than allowing a
    # dictionary key to overwrite earlier periods. The mutable schedule
    # fields (day, slot, room, etc.) remain part of the compared snapshot.
    def keyed_entries(entries):
        grouped={}
        for entry in sorted(entries, key=lambda e: (str(e.section_id), str(e.course_offering_id), e.weekday, str(e.start_slot_id))):
            pair=(str(entry.section_id), str(entry.course_offering_id))
            occurrence=len(grouped.setdefault(pair, []))
            grouped[pair].append(((pair[0], pair[1], occurrence), _entry_snapshot(entry)))
        return {key: snapshot for rows in grouped.values() for key, snapshot in rows}
    old=keyed_entries(old_entries); new=keyed_entries(new_entries)
    changes=[]
    for key in sorted(set(old)|set(new)):
        if key not in old: changes.append({'type':'ADDED','new':new[key]})
        elif key not in new: changes.append({'type':'REMOVED','old':old[key]})
        elif old[key]!=new[key]:
            fields={};
            for field in ('room','weekday','start_slot','block_length','course_offering','faculty'):
                if old[key][field]!=new[key][field]: fields[field]={'old':old[key][field],'new':new[key][field]}
            if set(fields)=={'faculty'}:
                for faculty_id in set(old[key]['faculty'])-set(new[key]['faculty']): changes.append({'type':'REMOVED','faculty_id':faculty_id,'old':old[key],'new':new[key],'course_code':new[key]['course_code'],'course_name':new[key]['course_name']})
                for faculty_id in set(new[key]['faculty'])-set(old[key]['faculty']): changes.append({'type':'ADDED','faculty_id':faculty_id,'old':old[key],'new':new[key],'course_code':new[key]['course_code'],'course_name':new[key]['course_name']})
            else: changes.append({'type':'CHANGED','course_code':new[key]['course_code'],'course_name':new[key]['course_name'],'old':old[key],'new':new[key],'changes':fields})
    grouped={}
    for change in changes:
        if change.get('faculty_id'):
            grouped.setdefault(change['faculty_id'],[]).append(change)
            continue
        old_fac=set(change.get('old',{}).get('faculty',())); new_fac=set(change.get('new',{}).get('faculty',()))
        for faculty_id in old_fac|new_fac: grouped.setdefault(faculty_id,[]).append(change)
    return changes,grouped
def notify_published_diff(old_version,new_version,actor=None):
    changes,grouped=publish_diff(old_version,new_version)
    user_ids=[x for x in grouped if x]
    from accounts.models import User
    from faculty.models import Faculty
    faculty_users={str(f.id):str(f.user_id) for f in Faculty.objects.filter(id__in=user_ids,user__isnull=False)}
    users={str(u.id):u for u in User.objects.filter(id__in=faculty_users.values(),is_active=True)}
    for faculty_id,items in grouped.items():
        user=users.get(faculty_users.get(faculty_id))
        if not user: continue
        if old_version is None: title='BBD Smart Scheduler - Timetable Published'; message=f'Your official timetable for {new_version.timetable.title} has been published.'; event='TIMETABLE_PUBLISHED'
        else:
            title='BBD Smart Scheduler - Your Timetable Has Been Updated'; event='TIMETABLE_UPDATED'; lines=[f'Your official timetable has been updated.', '', f'{len(items)} schedule change(s) were published.']
            for i,item in enumerate(items,1): lines.append(f'{i}. {item.get("new",item.get("old",{})).get("course_code","")} - {item.get("new",item.get("old",{})).get("course_name","")} ({item["type"]})')
            lines.append('');lines.append(f'Please review your latest timetable: {getattr(settings,"FRONTEND_URL","http://localhost:3000")}/my-timetable');message='\n'.join(lines)
        notify(event_type=event,recipients=[user],title=title,message=message,metadata={'version':new_version.version_no,'previous_version':old_version.version_no if old_version else None,'changes':items},actor=actor)
    return changes
