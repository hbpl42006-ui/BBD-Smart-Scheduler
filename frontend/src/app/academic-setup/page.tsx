'use client';
import Link from 'next/link';
import {BookOpen,Building2,CalendarDays,Clock3,GraduationCap,Landmark,Users,Layers3} from 'lucide-react';
import {AdminLayout} from '@/components/layout/AdminLayout';
import {Header} from '@/components/layout/Header';
import {Card,CardContent,CardHeader,CardTitle} from '@/components/ui/card';
const modules=[
 {step:1,title:'Academic Session',href:'/academic-setup/sessions',icon:CalendarDays,text:'Define the academic year, e.g. 2026-27.'},
 {step:2,title:'Semester',href:'/academic-setup/semesters',icon:Clock3,text:'Configure semester number, Odd/Even type and dates.'},
 {step:3,title:'Program',href:'/academic-setup/programs',icon:GraduationCap,text:'Create programs such as B.Tech Computer Science & Engineering.'},
 {step:4,title:'Section',href:'/academic-setup/sections',icon:Layers3,text:'Create class sections such as CS 1A and CS 1B.'},
 {step:5,title:'Courses',href:'/courses',icon:BookOpen,text:'Configure course codes and course names.'},
 {step:6,title:'Faculty',href:'/faculty',icon:Users,text:'Manage faculty profiles and teaching information.'},
 {step:7,title:'Rooms & Labs',href:'/rooms-labs',icon:Building2,text:'Configure room numbers, capacity and room type.'},
 {step:8,title:'Time Slots',href:'/time-slots',icon:Landmark,text:'Configure daily teaching periods and breaks.'},
];
export default function AcademicSetupPage(){return <AdminLayout><Header title="Academic Setup"/><main className="mx-auto w-full max-w-[1500px] space-y-6 p-5 sm:p-8"><div><h1 className="text-2xl font-semibold">Academic Setup</h1><p className="mt-1 text-sm text-slate-500">Configure the academic data required for timetable generation.</p></div><Card><CardHeader><CardTitle>Recommended setup order</CardTitle></CardHeader><CardContent className="grid gap-3 md:grid-cols-2">{modules.map(({step,title,href,icon:Icon,text})=><Link href={href} key={href} className="flex items-start gap-4 rounded-lg border p-4 transition-colors hover:bg-slate-50"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700"><Icon className="h-5 w-5"/></div><div><p className="text-xs font-medium uppercase tracking-wide text-slate-400">Step {step}</p><h2 className="font-semibold">{title}</h2><p className="mt-1 text-sm text-slate-500">{text}</p></div></Link>)}</CardContent></Card><div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900"><p className="font-semibold">Complete the academic setup before generating a timetable.</p><p className="mt-1">Timetable generation depends on configured sections, courses and course offerings, faculty, rooms, and time slots.</p></div></main></AdminLayout>}
