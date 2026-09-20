from audit.models import AuditEvent
import json
def clean(value): return json.loads(json.dumps(value, default=str))
def record(event_type, actor=None, timetable=None, version=None, entity_type='', entity_id=None, old_data=None, new_data=None, metadata=None):
    return AuditEvent.objects.create(event_type=event_type,actor=actor,timetable_id=getattr(timetable,'pk',timetable),version_id=getattr(version,'pk',version),entity_type=entity_type,entity_id=entity_id,old_data=clean(old_data or {}),new_data=clean(new_data or {}),metadata=clean(metadata or {}))
