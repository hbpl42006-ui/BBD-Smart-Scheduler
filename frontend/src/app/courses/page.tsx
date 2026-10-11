'use client';

import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { PageHeader } from '@/components/PageHeader';
import { ImportExportActions } from '@/components/ImportExportActions';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiClient } from '@/lib/api/client';
import { clearListCache, create, listAll, update, type Entity } from '@/lib/api/resources';
import { me } from '@/lib/api/auth';
import { canCreate, canEdit, canDeactivate, canImport, type Role } from '@/lib/permissions';

type Course = Entity & { code?: string; name?: string; short_code?: string; credit?: number; active?: boolean };

export default function Page() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [count, setCount] = useState(0);
  const [role, setRole] = useState<Role>();
  const [error, setError] = useState('');
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [editing, setEditing] = useState<Course | null>(null);
  const load = () => listAll<Course>('courses', { ...(search ? { search } : {}), status }).then((items) => { setCourses(items); setCount(items.length); }).catch(() => setError('Could not load courses.'));
  useEffect(() => { void load(); }, [search, status]);
  useEffect(() => { me().then((user) => setRole(user.role as Role)).catch(() => setRole(undefined)); }, []);
  const save = async (event: React.FormEvent) => { event.preventDefault(); setError(''); try { if (editing) await update('courses', editing.id, form); else await create('courses', form); clearListCache('courses'); setEditing(null); setForm({}); await load(); } catch { setError('Could not save course.'); } };
  const toggleArchive = async (course: Course) => { try { if (course.active) await apiClient.delete(`/courses/${course.id}/`); else await apiClient.post(`/courses/${course.id}/restore/`); clearListCache('courses'); await load(); } catch { setError('Could not update course status.'); } };
  const writable = canCreate(role), importable = canImport(role);
  const fields = [['code', 'Code'], ['name', 'Name'], ['short_code', 'Short code'], ['credit', 'Credit']];

  return <AdminLayout>
    <Header title="Courses" />
    <main className="mx-auto w-full max-w-[1500px] p-5 sm:p-8">
      <PageHeader title="Courses" description="Manage the complete course catalog." extraAction={<ImportExportActions kind="courses" endpoint="courses" canImport={importable} onComplete={load} params={{ search: search || undefined, status }} />} />
      <div className="mb-4 flex flex-wrap gap-2">
        <Input className="max-w-sm" placeholder="Search code, name or short code" value={search} onChange={(event) => setSearch(event.target.value)} />
        <label className="flex items-center gap-2 text-sm">Status<select className="h-9 rounded border px-2" value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All</option><option value="active">Active</option><option value="archived">Archived</option></select></label>
        {writable && <Button onClick={() => { setEditing(null); setForm({ code: '', name: '', short_code: '', credit: '' }); }}>Add</Button>}
      </div>
      {error && <p className="mb-3 rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      <Card><CardContent className="p-0"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b bg-slate-50"><tr>{['Code', 'Name', 'Short Code', 'Credit', 'Status', 'Actions'].map((heading) => <th key={heading} className="p-3">{heading}</th>)}</tr></thead><tbody>{courses.map((course) => <tr key={course.id} className="border-b"><td className="p-3">{course.code}</td><td className="p-3">{course.name}</td><td className="p-3">{course.short_code}</td><td className="p-3">{course.credit}</td><td className="p-3">{course.active ? 'Active' : 'Archived'}</td><td className="p-3">{canEdit(role) && <Button variant="ghost" size="sm" onClick={() => { setEditing(course); setForm({ code: course.code, name: course.name, short_code: course.short_code, credit: course.credit }); }}>Edit</Button>}{canDeactivate(role) && <Button variant="ghost" size="sm" onClick={() => void toggleArchive(course)}>{course.active ? 'Archive' : 'Restore'}</Button>}</td></tr>)}</tbody></table></div><p className="border-t p-3 text-xs text-slate-500">{count} total records</p></CardContent></Card>
      {(editing || Object.keys(form).length > 0) && <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4"><form onSubmit={save} className="w-full max-w-lg space-y-4 rounded-xl bg-white p-6"><h2 className="text-lg font-semibold">{editing ? 'Edit' : 'Add'} Course</h2>{fields.map(([key, label]) => <label key={key} className="block text-sm">{label}<Input required value={String(form[key] ?? '')} type={key === 'credit' ? 'number' : 'text'} onChange={(event) => setForm({ ...form, [key]: key === 'credit' ? Number(event.target.value) : event.target.value })} /></label>)}<div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => { setEditing(null); setForm({}); }}>Cancel</Button><Button type="submit">Save</Button></div></form></div>}
    </main>
  </AdminLayout>;
}
