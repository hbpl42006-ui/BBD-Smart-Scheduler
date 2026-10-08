'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent } from '@/components/ui/card';
import { FacultyWorkloadGrid, WorkloadPrintButton } from '@/components/FacultyWorkloadGrid';
import { me } from '@/lib/api/auth';
import { schedulingApi, FacultyTimetableResponse } from '@/lib/api/scheduling';

export default function MyWorkloadPage() {
  const router = useRouter(); const [data, setData] = useState<FacultyTimetableResponse | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  useEffect(() => { me().then(user => { if (user.role !== 'FACULTY') { router.replace('/dashboard'); return; } schedulingApi.myWorkload().then(setData).catch(reason => { const code = (reason as { response?: { data?: { code?: string } } }).response?.data?.code; setError(code === 'FACULTY_PROFILE_NOT_LINKED' ? 'Your account is not linked to a Faculty profile. Please contact your administrator.' : 'Unable to load your workload.'); }).finally(() => setLoading(false)); }).catch(() => router.replace('/login')); }, [router]);
  return <AdminLayout><Header title="My Workload" /><main className="p-5 sm:p-8"><div className="mb-4 flex items-center justify-between no-print"><div><h1 className="text-2xl font-semibold">My Workload</h1><p className="text-sm text-slate-500">Your complete weekly teaching load across assigned sections.</p></div>{data?.version && <WorkloadPrintButton />}</div>{loading && <Card><CardContent className="p-8">Loading your workload...</CardContent></Card>}{error && <p className="rounded bg-red-50 p-3 text-red-700">{error}</p>}{data && !data.version && <Card><CardContent className="p-8 text-center">No published timetable is available yet.</CardContent></Card>}{data?.version && <FacultyWorkloadGrid data={data} />}</main></AdminLayout>;
}
