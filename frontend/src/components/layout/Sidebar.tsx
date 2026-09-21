'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { me } from '@/lib/api/auth';
import type { Role } from '@/lib/permissions';
import { canReviewTimetables } from '@/lib/permissions';
import {
  BookOpen,
  LayoutDashboard,
  Settings,
  Users,
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
  { title: 'My Timetable', url: '/my-timetable', icon: CalendarDays },
  {
    title: 'Dashboard',
    url: '/dashboard',
    icon: LayoutDashboard,
  },
  {
    title: 'Timetables',
    url: '/timetables',
    icon: Table,
  },
  { title: 'Approvals', url: '/approvals', icon: FileText },
  {
    title: 'Room Allocation',
    url: '/room-allocation',
    icon: Building,
  },
  {
    title: 'Academic Setup',
    url: '/academic-setup',
    icon: Settings,
    items: [
      { title: 'Sessions', url: '/academic-setup/sessions' },
      { title: 'Semesters', url: '/academic-setup/semesters' },
      { title: 'Programs', url: '/academic-setup/programs' },
      { title: 'Sections', url: '/academic-setup/sections' },
    ],
  },
  {
    title: 'Courses',
    url: '/courses',
    icon: BookOpen,
  },
  {
    title: 'Faculty',
    url: '/faculty',
    icon: Users,
  },
  {
    title: 'Rooms & Labs',
    url: '/rooms-labs',
    icon: Building,
  },
  {
    title: 'Time Slots',
    url: '/time-slots',
    icon: Clock,
  },
  {
    title: 'Notifications',
    url: '/notifications',
    icon: Bell,
  },
  {
    title: 'Reports',
    url: '/reports',
    icon: FileText,
  },
];

export function AppSidebar() {
  const pathname = usePathname();
  const [role, setRole] = React.useState<Role>();
  React.useEffect(() => { me().then(user => setRole(user.role as Role)).catch(() => setRole(undefined)); }, []);
  const visibleItems = navItems.filter(item => (item.url !== '/my-timetable' || role === 'FACULTY') && (item.url !== '/approvals' || canReviewTimetables(role)));

  return (
    <Sidebar className="no-print">
      <SidebarHeader className="border-b border-slate-200">
        <div className="flex items-center gap-3 px-4 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-700 text-sm font-bold text-white">B</div>
          <div><div className="font-semibold tracking-tight">BBD Smart Scheduler</div><div className="text-[10px] uppercase tracking-wider text-slate-500">Academic management</div></div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="px-3 text-[10px] uppercase tracking-widest text-slate-400">Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {visibleItems.map((item) => (
                <SidebarMenuItem key={item.title}>
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
