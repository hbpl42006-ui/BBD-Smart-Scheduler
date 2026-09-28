import os
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from audit.models import AuditEvent
from accounts.models import Role
from .data_reset import execute, preview

def _admin(request): return request.user.is_superuser or request.user.role == Role.SUPER_ADMIN

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def data_reset_preview(request):
    if not _admin(request): return Response({'detail': 'Only administrators may reset academic data.'}, status=403)
    try: return Response(preview(str(request.data.get('mode', '')).strip().upper()))
    except ValueError as exc: return Response({'detail': str(exc)}, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def data_reset_execute(request):
    if not _admin(request): return Response({'detail': 'Only administrators may reset academic data.'}, status=403)
    if os.environ.get('ALLOW_DESTRUCTIVE_DATA_RESET', 'false').lower() not in ('1', 'true', 'yes'):
        return Response({'detail': 'Destructive data reset is disabled by server configuration.'}, status=403)
    mode = str(request.data.get('mode', '')).strip().upper()
    expected = 'RESET SCHEDULING DATA' if mode == 'SCHEDULING_ONLY' else 'RESET ACADEMIC DATA'
    if request.data.get('confirmation') != expected: return Response({'detail': f'Confirmation must exactly equal "{expected}".'}, status=400)
    try: deleted = execute(mode)
    except ValueError as exc: return Response({'detail': str(exc)}, status=400)
    AuditEvent.objects.create(event_type='ACADEMIC_DATA_RESET', actor=request.user, entity_type='DATA_RESET', metadata={'mode': mode, 'deleted': deleted})
    return Response({'success': True, 'mode': mode, 'deleted': deleted})
