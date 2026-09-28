import io, csv
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from accounts.models import User, Role
from institutions.models import Institution
from rooms.models import Room
from common.serializers import FacultySerializer
from faculty.models import Faculty
from institutions.models import Department

@pytest.fixture
def client(): return APIClient()
@pytest.fixture
def user(db): return User.objects.create_user('admin@test.local','StrongPass123!',role=Role.SUPER_ADMIN)
def auth(client,user):
    response=client.post('/api/auth/login/',{'email':user.email,'password':'StrongPass123!'},format='json'); client.credentials(HTTP_AUTHORIZATION='Bearer '+response.data['access'])
def test_auth_and_me(client,user):
    auth(client,user); assert client.get('/api/auth/me/').status_code==200
def test_read_only_cannot_write(client,db):
    user=User.objects.create_user('viewer@test.local','StrongPass123!',role=Role.READ_ONLY_VIEWER); auth(client,user)
    assert client.post('/api/institutions/',{'name':'x','code':'x'},format='json').status_code==403
def test_seeded_dashboard(client,user):
    auth(client,user); assert client.get('/api/dashboard/summary/').status_code==200

def test_dashboard_faculty_availability_uses_default_available_semantics(client,user,db):
    auth(client,user)
    response = client.get('/api/dashboard/summary/')
    assert response.status_code == 200
    assert response.data['readiness']['faculty_availability'] is True
    assert response.data['faculty_availability_status'] == 'DEFAULT'
    assert response.data['faculty_availability_restrictions'] == 0

def test_dashboard_faculty_availability_reports_configured_restrictions(client,user,db):
    from common.models import TimeSlot, TimeSlotTemplate
    from faculty.models import FacultyAvailability
    institution = Institution.objects.create(name='Availability Institute', code='AVAIL')
    department = Department.objects.create(institution=institution, name='Availability Department', code='AV')
    faculty = Faculty.objects.create(employee_code='AV001', initials='AV', department=department)
    template = TimeSlotTemplate.objects.create(name='Availability Template', institution=institution)
    from datetime import time
    slot = TimeSlot.objects.create(template=template, label='09:00-10:00', start_time=time(9), end_time=time(10), order=1)
    FacultyAvailability.objects.create(faculty=faculty, weekday=0, time_slot=slot, is_available=False)
    auth(client,user)
    response = client.get('/api/dashboard/summary/')
    assert response.data['readiness']['faculty_availability'] is True
    assert response.data['faculty_availability_status'] == 'CONFIGURED'
    assert response.data['faculty_availability_restrictions'] == 1
def test_room_filter(client,user,db):
    Room.objects.create(code='R1',building='B',floor='1',capacity=30,room_type='CLASSROOM'); auth(client,user)
    assert client.get('/api/rooms/?building=B').data['count']==1
def test_import_preview_does_not_write(client,user,db):
    auth(client,user); body=b'code,building,floor,capacity,room_type,active\nIMP1,B,1,30,CLASSROOM,true\n'
    upload=SimpleUploadedFile('rooms.csv',body,content_type='text/csv'); response=client.post('/api/imports/rooms/preview/',{'file':upload},format='multipart')
    assert response.status_code==200 and response.data['valid'] and not Room.objects.filter(code='IMP1').exists()
def test_import_commit(client,user,db):
    auth(client,user); body=b'code,building,floor,capacity,room_type,active\nIMP2,B,1,30,CLASSROOM,true\n'
    upload=SimpleUploadedFile('rooms.csv',body,content_type='text/csv'); assert client.post('/api/imports/rooms/commit/',{'file':upload},format='multipart').status_code==201
    assert Room.objects.filter(code='IMP2').exists()

def test_room_import_supports_user_headers_and_template(client,user,db):
    auth(client,user); body=b'Room No.,Building,Floor,Capacity,Room Type,Active\n401,Main,4,60,Classroom,true\n402,Main,4,40,Computer Lab,true\n'
    upload=SimpleUploadedFile('rooms.csv',body,content_type='text/csv'); response=client.post('/api/imports/rooms/commit/',{'file':upload},format='multipart')
    assert response.status_code==201 and Room.objects.filter(code='401',room_type='CLASSROOM').exists() and Room.objects.filter(code='402',room_type='COMPUTER_LAB').exists()
    template=client.get('/api/imports/rooms/template/'); assert template.status_code==200 and template['Content-Type'].startswith('application/vnd.openxmlformats')

def test_room_import_skips_duplicate_without_creating_another_record(client,user,db):
    Room.objects.create(code='401',building='Main',floor='4',capacity=60,room_type='CLASSROOM')
    auth(client,user); body=b'Room No.,Building,Floor,Capacity,Room Type,Active\n401,Main,4,60,Classroom,true\n403,Main,4,60,Classroom,true\n'
    upload=SimpleUploadedFile('rooms.csv',body,content_type='text/csv'); response=client.post('/api/imports/rooms/commit/',{'file':upload},format='multipart')
    assert response.status_code==201 and response.data['created']==1 and response.data['skipped']==1 and Room.objects.filter(code='401').count()==1
def test_schema(client): assert client.get('/api/schema/').status_code==200

def test_faculty_serializer_handles_linked_and_unlinked_faculty(db):
    institution = Institution.objects.create(name='Test Institute', code='TEST')
    department = Department.objects.create(institution=institution, name='Computer Science', code='CSE')
    linked = User.objects.create_user('linked-faculty@test.local', 'StrongPass123!', first_name='Aarav', last_name='Sharma')
    Faculty.objects.create(user=linked, employee_code='F001', initials='AS', department=department)
    Faculty.objects.create(user=None, employee_code='F002', initials='RK', department=department)
    data = FacultySerializer(Faculty.objects.order_by('employee_code'), many=True).data
    assert data[0]['name'] == 'Aarav Sharma'
    assert data[0]['email'] == linked.email
    assert data[1]['name'] == 'RK'
    assert data[1]['email'] is None

def test_faculty_endpoint_returns_mixed_linked_and_unlinked_records(client, user, db):
    institution = Institution.objects.create(name='API Institute', code='API')
    department = Department.objects.create(institution=institution, name='Engineering', code='ENG')
    linked = User.objects.create_user('api-faculty@test.local', 'StrongPass123!', first_name='Mira', last_name='Das')
    Faculty.objects.create(user=linked, employee_code='AF001', initials='MD', department=department)
    Faculty.objects.create(user=None, employee_code='AF002', initials='NP', department=department)
    before = User.objects.count()
    auth(client, user)
    response = client.get('/api/faculty/')
    assert response.status_code == 200
    records = response.data['results'] if isinstance(response.data, dict) and 'results' in response.data else response.data
    assert {record['name'] for record in records} >= {'Mira Das', 'NP'}
    assert User.objects.count() == before

def test_room_projector_api_and_import_values(client,user,db):
    auth(client,user)
    room=Room.objects.create(code='PJ1',building='Main',floor='1',capacity=40,room_type='CLASSROOM',has_projector=True)
    response=client.get(f'/api/rooms/{room.pk}/')
    assert response.status_code==200 and response.data['has_projector'] is True
    body=b'Room No.,Building,Floor,Capacity,Room Type,Projector,Active\nPJ2,Main,1,40,Classroom,Yes,true\nPJ3,Main,1,40,Classroom,0,true\n'
    upload=SimpleUploadedFile('projectors.csv',body,content_type='text/csv')
    assert client.post('/api/imports/rooms/commit/',{'file':upload},format='multipart').status_code==201
    assert Room.objects.get(code='PJ2').has_projector is True and Room.objects.get(code='PJ3').has_projector is False

def test_room_projector_import_rejects_invalid_value(client,user,db):
    auth(client,user); body=b'Room No.,Building,Floor,Capacity,Room Type,Projector,Active\nPJ4,Main,1,40,Classroom,maybe,true\n'
    upload=SimpleUploadedFile('invalid-projector.csv',body,content_type='text/csv'); response=client.post('/api/imports/rooms/preview/',{'file':upload},format='multipart')
    assert response.status_code==200 and response.data['valid'] is False and 'Projector must be' in response.data['errors'][0]['message']
