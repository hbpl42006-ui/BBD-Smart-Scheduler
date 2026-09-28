import pytest
from rest_framework.test import APIClient
from accounts.models import Role, User
from academics.models import Course

@pytest.fixture
def reset_admin(db): return User.objects.create_user('reset-admin@test.local', 'StrongPass123!', role=Role.SUPER_ADMIN)

def login(client, user):
    response = client.post('/api/auth/login/', {'email': user.email, 'password': 'StrongPass123!'}, format='json')
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

def test_data_reset_preview_is_read_only(reset_admin, db):
    Course.objects.create(code='RESET1', name='Reset test', short_code='R1', credit='3.0')
    client = APIClient(); login(client, reset_admin)
    before = Course.objects.count()
    response = client.post('/api/admin/data-reset/preview/', {'mode': 'ACADEMIC_DATA_ONLY'}, format='json')
    assert response.status_code == 200
    assert response.data['will_delete']['courses'] == before
    assert Course.objects.count() == before

def test_data_reset_requires_super_admin(reset_admin, db):
    user = User.objects.create_user('reset-viewer@test.local', 'StrongPass123!', role=Role.TIMETABLE_COORDINATOR)
    client = APIClient(); login(client, user)
    assert client.post('/api/admin/data-reset/preview/', {'mode': 'ACADEMIC_DATA_ONLY'}, format='json').status_code == 403

def test_data_reset_execute_requires_flag_and_exact_confirmation(reset_admin, db, monkeypatch):
    client = APIClient(); login(client, reset_admin)
    monkeypatch.delenv('ALLOW_DESTRUCTIVE_DATA_RESET', raising=False)
    response = client.post('/api/admin/data-reset/execute/', {'mode': 'ACADEMIC_DATA_ONLY', 'confirmation': 'RESET ACADEMIC DATA'}, format='json')
    assert response.status_code == 403
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_DATA_RESET', 'true')
    response = client.post('/api/admin/data-reset/execute/', {'mode': 'ACADEMIC_DATA_ONLY', 'confirmation': 'wrong'}, format='json')
    assert response.status_code == 400
