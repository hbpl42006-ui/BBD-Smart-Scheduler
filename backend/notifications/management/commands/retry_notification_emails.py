from django.core.management.base import BaseCommand
from notifications.models import NotificationDelivery
from notifications.services import send_delivery,send_whatsapp
class Command(BaseCommand):
    help='Retry pending or failed notification email deliveries.'
    def add_arguments(self,parser):
        parser.add_argument('--max-attempts',type=int,default=3)
        parser.add_argument('--channel',choices=['email','whatsapp','all'],default='all')
    def handle(self,*args,**opts):
        qs=NotificationDelivery.objects.filter(status__in=['PENDING','FAILED'],attempts__lt=opts['max_attempts'])
        if opts['channel']!='all': qs=qs.filter(channel=opts['channel'].upper())
        count=0
        for delivery in qs:
            (send_whatsapp if delivery.channel=='WHATSAPP' else send_delivery)(delivery);count+=1
        self.stdout.write(f'Retried {count} notification email deliveries.')
