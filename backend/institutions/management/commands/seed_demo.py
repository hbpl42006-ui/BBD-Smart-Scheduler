from django.core.management.base import BaseCommand
from datetime import date, time
from accounts.models import User, Role
from institutions.models import Institution, Department, Program
from academics.models import AcademicSession, Semester, Section, Course, CourseOffering
from faculty.models import Faculty
from rooms.models import Room
from common.models import TimeSlotTemplate, TimeSlot
class Command(BaseCommand):
    help = 'Seed demo data'
    def handle(self, *args, **kwargs):
        inst, _ = Institution.objects.update_or_create(code='BBDU', defaults={'name':'Babu Banarasi Das University','timezone':'Asia/Kolkata'})
        dept, _ = Department.objects.update_or_create(code='CSE', defaults={'institution':inst,'name':'Computer Science & Engineering'})
        programs=[]
        for code,name,duration in [('BTECH-CSE','B.Tech Computer Science & Engineering',4),('BCA','Bachelor of Computer Applications',3)]:
            p,_=Program.objects.update_or_create(code=code,defaults={'department':dept,'name':name,'duration_years':duration}); programs.append(p)
        session,_=AcademicSession.objects.update_or_create(institution=inst,name='2026-27',defaults={'start_date':date(2026,7,1),'end_date':date(2027,6,30),'is_active':True})
        sem,_=Semester.objects.update_or_create(session=session,number=1,defaults={'name':'Odd Semester','type':'ODD','start_date':date(2026,7,1),'end_date':date(2026,12,31)})
        for p in programs:
            Section.objects.get_or_create(program=p,semester=sem,year=1,name='A',defaults={'student_strength':60})
        template,_=TimeSlotTemplate.objects.update_or_create(institution=inst,name='Standard Day')
        slots=[('09:00-10:00',9,10,False),('10:00-11:00',10,11,False),('11:00-12:00',11,12,False),('12:00-13:00',12,13,False),('13:00-14:00',13,14,True),('14:00-15:00',14,15,False),('15:00-16:00',15,16,False),('16:00-17:00',16,17,False)]
        for i,(label,start,end,brk) in enumerate(slots,1): TimeSlot.objects.update_or_create(template=template,order=i,defaults={'label':label,'start_time':time(start),'end_time':time(end),'is_break':brk})
        for code,name,short in [('CS101','Programming Fundamentals','PF'),('CS102','Data Structures','DS'),('MA101','Engineering Mathematics','EM')]: Course.objects.update_or_create(code=code,defaults={'name':name,'short_code':short,'credit':3})
        for code in ['CSE101','CSE102','CSE103']: Room.objects.update_or_create(code=code,defaults={'building':'Engineering Block','floor':'1','capacity':60,'room_type':Room.RoomType.CLASSROOM})
        lab,_=Room.objects.update_or_create(code='CSE-LAB-1',defaults={'building':'Engineering Block','floor':'1','capacity':40,'room_type':Room.RoomType.COMPUTER_LAB})
        user,_=User.objects.get_or_create(email='faculty.demo@bbdu.ac.in',defaults={'first_name':'Aarav','last_name':'Sharma','role':Role.FACULTY}); user.set_password('DemoPass123!'); user.save()
        Faculty.objects.update_or_create(employee_code='FAC001',defaults={'user':user,'initials':'AS','department':dept})
        for section in Section.objects.filter(semester=sem)[:1]:
            for course in Course.objects.all(): CourseOffering.objects.get_or_create(semester=sem,section=section,course=course,defaults={'weekly_periods':3,'default_class_type':'LECTURE','required_block_size':1,'active':True})
        self.stdout.write(self.style.SUCCESS('Successfully seeded demo data'))
