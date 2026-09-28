from io import BytesIO
from openpyxl import Workbook
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from common.permissions import RolePermission
from .timeslot_import import prepare, commit

@api_view(['POST'])
@permission_classes([RolePermission])
def timeslot_import(request, action):
    upload = request.FILES.get('file')
    if not upload or not upload.name.lower().endswith(('.csv', '.xlsx')): return Response({'detail': 'Upload a CSV or XLSX file.'}, status=400)
    try: result = prepare(upload) if action == 'preview' else commit(upload)
    except Exception as exc: return Response({'detail': str(exc)}, status=400)
    if result['invalid']: return Response(result, status=400 if action == 'commit' else 200)
    return Response(result, status=201 if action == 'commit' else 200)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timeslot_import_template(request):
    book = Workbook(); sheet = book.active; sheet.title = 'Time Slots'
    sheet.append(['Template', 'Label', 'Start Time', 'End Time', 'Order', 'Is Break'])
    for row in [('Default Academic Day', '09:00-10:00', '09:00', '10:00', 1, 'No'), ('Default Academic Day', '10:00-11:00', '10:00', '11:00', 2, 'No'), ('Default Academic Day', '11:00-12:00', '11:00', '12:00', 3, 'No'), ('Default Academic Day', '12:00-13:00', '12:00', '13:00', 4, 'No'), ('Default Academic Day', '13:00-14:00', '13:00', '14:00', 5, 'Yes'), ('Default Academic Day', '14:00-15:00', '14:00', '15:00', 6, 'No'), ('Default Academic Day', '15:00-16:00', '15:00', '16:00', 7, 'No'), ('Default Academic Day', '16:00-17:00', '16:00', '17:00', 8, 'No')]: sheet.append(row)
    stream = BytesIO(); book.save(stream)
    return HttpResponse(stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="time-slots-import-template.xlsx"'})
