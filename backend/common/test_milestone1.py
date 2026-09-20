import io, csv
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from accounts.models import User, Role
from institutions.models import Institution
from rooms.models import Room

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
