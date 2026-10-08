'use client';
import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { FacultyWorkloadGrid, WorkloadPrintButton } from '@/components/FacultyWorkloadGrid';
import { me } from '@/lib/api/auth';
import { schedulingApi, FacultyTimetableResponse } from '@/lib/api/scheduling';

type FacultyOption = { id: string; name: string; employee_code?: string | null; department: string };
export default function FacultyWorkloadPage() {
  const [options, setOptions] = useState<FacultyOption[]>([]); const [selected, setSelected] = useState(''); const [query, setQuery] = useState(''); const [data, setData] = useState<FacultyTimetableResponse | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  useEffect(() => { me().then(user => { if (!['SUPER_ADMIN', 'HOD_OR_DEAN_APPROVER'].includes(user.role)) { setError('You do not have permission to view Faculty workloads.'); setLoading(false); return; } schedulingApi.facultyWorkloadList().then(setOptions).catch(() => setError('Unable to load permitted Faculty.')).finally(() => setLoading(false)); }).catch(() => { setError('Unable to load your account.'); setLoading(false); }); }, []);
  useEffect(() => { if (!selected) return; let active = true; schedulingApi.facultyWorkload(selected).then(result => { if (active) setData(result); }).catch(() => { if (active) { setData(null); setError('Unable to load this published Faculty workload.'); } }); return () => { active = false; }; }, [selected]);
  const filtered = options.filter(item => `${item.name} ${item.employee_code ?? ''} ${item.department}`.toLowerCase().includes(query.toLowerCase()));
  return <AdminLayout><Header title="Faculty Workload" /><main className="p-5 sm:p-8"><div className="mb-4 flex items-center justify-between no-print"><div><h1 className="text-2xl font-semibold">Faculty Workload</h1><p className="text-sm text-slate-500">Published weekly teaching load, grouped by Faculty member.</p></div>{data?.version && <WorkloadPrintButton />}</div><Card className="mb-5 no-print"><CardContent className="grid gap-4 p-5 md:grid-cols-2"><label className="text-sm font-medium">Search Faculty<Input className="mt-1" value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, employee code or department" /></label><label className="text-sm font-medium">Select Faculty<select className="mt-1 w-full rounded border p-2" value={selected} onChange={event => { setSelected(event.target.value); setData(null); setError(''); }}><option value="">Select Faculty</option>{filtered.map(item => <option key={item.id} value={item.id}>{item.name} - {item.employee_code ?? item.department}</option>)}</select></label></CardContent></Card>{loading && <Card><CardContent className="p-8">Loading Faculty...</CardContent></Card>}{error && <p className="mb-4 rounded bg-red-50 p-3 text-red-700">{error}</p>}{data && !data.version && <Card><CardContent className="p-8 text-center">No published timetable is available for this Faculty member.</CardContent></Card>}{data?.version && <FacultyWorkloadGrid data={data} />}</main></AdminLayout>;
}
