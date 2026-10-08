'use client';

import * as React from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { me } from '@/lib/api/auth';
import type { Role } from '@/lib/permissions';
import { canReviewTimetables } from '@/lib/permissions';
import {
  LayoutDashboard,
  Settings,
  Building,
  Clock,
  Table,
  Bell,
  FileText
  ,CalendarDays
} from 'lucide-react';

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar';

const navItems = [
  { title: 'Dashboard', url: '/dashboard', icon: LayoutDashboard, group: 'Workspace' },
  { title: 'My Timetable', url: '/my-timetable', icon: CalendarDays, group: 'Scheduling' },
  { title: 'My Workload', url: '/my-workload', icon: CalendarDays, group: 'Scheduling' },
  { title: 'Academic Setup', url: '/academic-setup', icon: Settings, group: 'Academic Setup', items: [
    { title: 'Sessions', url: '/academic-setup/sessions' }, { title: 'Semesters', url: '/academic-setup/semesters' },
    { title: 'Programs', url: '/academic-setup/programs' }, { title: 'Courses', url: '/courses' },
    { title: 'Sections', url: '/academic-setup/sections' }, { title: 'Faculty', url: '/faculty' },
    { title: 'Rooms & Labs', url: '/rooms-labs' }, { title: 'Time Slots', url: '/time-slots' },
    { title: 'Course Offerings', url: '/course-offerings' },
  ] },
  { title: 'Faculty Availability', url: '/faculty-arrangements/availability', icon: Clock, group: 'Scheduling' },
  { title: 'Timetables', url: '/timetables', icon: Table, group: 'Scheduling' },
  { title: 'Section Timetables', url: '/section-timetables', icon: CalendarDays, group: 'Scheduling' },
  { title: 'Room Allocation', url: '/room-allocation', icon: Building, group: 'Scheduling' },
  { title: 'Faculty Arrangements', url: '/faculty-arrangements', icon: CalendarDays, group: 'Operations', items: [
    { title: 'Arrangements', url: '/faculty-arrangements' },
    { title: 'Faculty Availability', url: '/faculty-arrangements/availability' },
  ] },
  { title: 'Faculty Workload', url: '/faculty-workload', icon: CalendarDays, group: 'Scheduling' },
  { title: 'Reports', url: '/reports', icon: FileText, group: 'Reporting' },
  { title: 'Notifications', url: '/notifications', icon: Bell, group: 'System' },
  { title: 'Data Reset', url: '/system/data-reset', icon: Settings, group: 'System' },
  { title: 'Approvals', url: '/approvals', icon: FileText, group: 'System' },
];

export function AppSidebar() {
  const pathname = usePathname();
  const [role, setRole] = React.useState<Role>();
  React.useEffect(() => { me().then(user => setRole(user.role as Role)).catch(() => setRole(undefined)); }, []);
  const visibleItems = navItems.filter(item => (item.url !== '/my-timetable' && item.url !== '/my-workload' || role === 'FACULTY') && (item.url !== '/faculty-workload' || ['SUPER_ADMIN', 'HOD_OR_DEAN_APPROVER'].includes(role ?? '')) && (item.url !== '/section-timetables' || ['SUPER_ADMIN', 'HOD_OR_DEAN_APPROVER'].includes(role ?? '')) && (item.url !== '/approvals' || canReviewTimetables(role)) && (item.url !== '/system/data-reset' || role === 'SUPER_ADMIN'));

  return (
    <Sidebar className="no-print">
      <SidebarHeader className="border-b border-slate-200">
        <div className="flex items-center gap-3 px-4 py-5">
          <Image src="/logo.png" alt="BBD Smart Scheduler" width={36} height={36} className="h-9 w-9 rounded-lg object-contain" priority />
          <div><div className="font-semibold tracking-tight">BBD Smart Scheduler</div><div className="text-[10px] uppercase tracking-wider text-slate-500">Academic management</div></div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="px-3 text-[10px] uppercase tracking-widest text-slate-400">Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {visibleItems.map((item, index) => (
                <SidebarMenuItem key={item.title}>
                  {(index===0 || visibleItems[index-1].group!==item.group) && <SidebarGroupLabel className="mt-3 px-2 text-[10px] uppercase tracking-widest text-slate-400">{item.group}</SidebarGroupLabel>}
                  <SidebarMenuButton isActive={pathname === item.url || !!(item.items && pathname.startsWith(item.url))}>
                    <Link href={item.url} className="flex w-full items-center gap-2">
                      <item.icon className="h-4 w-4" />
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                  {item.items && (
                    <SidebarMenu className="pl-4 mt-1 space-y-1">
                      {item.items.map((subItem) => (
                        <SidebarMenuItem key={subItem.title}>
                          <SidebarMenuButton isActive={pathname === subItem.url}>
                            <Link href={subItem.url} className="flex w-full items-center">
                              <span>{subItem.title}</span>
                            </Link>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      ))}
                    </SidebarMenu>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
