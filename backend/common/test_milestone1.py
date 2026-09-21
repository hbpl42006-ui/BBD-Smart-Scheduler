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
