'use client';
import { useEffect, useState } from 'react';
import axios from 'axios';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { PageHeader } from '@/components/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { BulkImportDialog } from '@/components/BulkImportDialog';
import { apiClient } from '@/lib/api/client';
import { clearListCache, create, listAll, update, type Entity } from '@/lib/api/resources';
import { me } from '@/lib/api/auth';
import { canCreate, canEdit, canDeactivate, canImport, type Role } from '@/lib/permissions';
type Faculty = Entity & { name?: string; employee_code?: string; initials?: string; email?: string | null; department?: string; active?: boolean; has_login?: boolean };
type Department = Entity & { name?: string; code?: string };
type ApiErrorData = Record<string, unknown>;
const apiErrorMessage = (error: unknown, fallback: string) => {
  if (!axios.isAxiosError(error)) return fallback;
  const data = error.response?.data;
  if (!data) return fallback;
  if (typeof data === 'string') return data;
  if (typeof data !== 'object') return fallback;
  const values = data as ApiErrorData;
  if (typeof values.detail === 'string') return values.detail;
  if (Array.isArray(values.non_field_errors)) return values.non_field_errors.join(' ');
  for (const [field, value] of Object.entries(values)) {
    if (Array.isArray(value) && value.length) return `${field}: ${value.join(' ')}`;
    if (typeof value === 'string') return `${field}: ${value}`;
  }
  return fallback;
};
export default function Page() {
  const [items, setItems] = useState<Faculty[]>([]), [departments, setDepartments] = useState<Department[]>([]), [search, setSearch] = useState(''), [status, setStatus] = useState('all'), [count, setCount] = useState(0), [role, setRole] = useState<Role>(), [error, setError] = useState(''), [form, setForm] = useState<Record<string, unknown>>({}), [editing, setEditing] = useState<Faculty | null>(null);
  const load = () => Promise.all([listAll<Faculty>('faculty', { ...(search ? { search } : {}), status }), listAll<Department>('departments')]).then(([faculty, deps]) => { setItems(faculty); setCount(faculty.length); setDepartments(deps); }).catch(() => setError('Could not load faculty.'));
  useEffect(() => { void load(); }, [search, status]);
  useEffect(() => { me().then((user) => setRole(user.role as Role)).catch(() => setRole(undefined)); }, []);
  const save = async (event: React.FormEvent) => { event.preventDefault(); setError(''); try { if (editing) await update('faculty', editing.id, form); else await create('faculty', form); clearListCache('faculty'); setEditing(null); setForm({}); await load(); } catch (error: unknown) { console.log('FACULTY SAVE ERROR', { status: axios.isAxiosError(error) ? error.response?.status : undefined, data: axios.isAxiosError(error) ? error.response?.data : undefined, message: error instanceof Error ? error.message : String(error) }); setError(apiErrorMessage(error, 'Could not save faculty.')); } };
  const toggle = async (item: Faculty) => { try { if (item.active) await apiClient.delete(`/faculty/${item.id}/`); else await apiClient.post(`/faculty/${item.id}/restore/`); clearListCache('faculty'); await load(); } catch { setError('Could not update faculty status.'); } };
  const departmentName = (id?: string) => departments.find((d) => d.id === id)?.name || departments.find((d) => d.id === id)?.code || id || '—';
  const writable = canCreate(role), importable = canImport(role), fields = [['name', 'Faculty Name'], ['employee_code', 'Employee Code'], ['initials', 'Initials'], ['department', 'Department'], ['email', 'Email *']];
  return <AdminLayout><Header title="Faculty" /><main className="mx-auto w-full max-w-[1500px] p-5 sm:p-8"><PageHeader title="Faculty" description="Manage faculty profiles and optional login accounts." /><div className="mb-4 flex flex-wrap gap-2"><Input className="max-w-sm" placeholder="Search name, code, initials or email" value={search} onChange={(e) => setSearch(e.target.value)} /><label className="flex items-center gap-2 text-sm">Status<select className="h-9 rounded border px-2" value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">All</option><option value="active">Active</option><option value="archived">Archived</option></select></label>{writable && <Button onClick={() => setForm({ name: '', employee_code: '', initials: '', department: '', email: '' })}>Add</Button>}{importable && <BulkImportDialog kind="faculty" onComplete={load} />}</div>{error && <p className="mb-3 rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}<Card><CardContent className="p-0"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b bg-slate-50"><tr>{['Faculty Name', 'Employee Code', 'Initials', 'Email', 'Department', 'Login', 'Status', 'Actions'].map((h) => <th key={h} className="p-3">{h}</th>)}</tr></thead><tbody>{items.map((item) => <tr key={item.id} className="border-b"><td className="p-3">{item.name}</td><td className="p-3">{item.employee_code}</td><td className="p-3">{item.initials}</td><td className="p-3">{item.email || '—'}</td><td className="p-3">{departmentName(item.department)}</td><td className="p-3">{item.has_login ? 'Yes' : 'No'}</td><td className="p-3">{item.active ? 'Active' : 'Archived'}</td><td className="p-3">{canEdit(role) && <Button variant="ghost" size="sm" onClick={() => { setEditing(item); setForm({ name: item.name, employee_code: item.employee_code, initials: item.initials, department: item.department, email: item.email || '' }); }}>Edit</Button>}{canDeactivate(role) && <Button variant="ghost" size="sm" onClick={() => void toggle(item)}>{item.active ? 'Archive' : 'Restore'}</Button>}</td></tr>)}</tbody></table></div><p className="border-t p-3 text-xs text-slate-500">{count} total records</p></CardContent></Card>{(editing || Object.keys(form).length > 0) && <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4"><form onSubmit={save} className="max-h-[90vh] w-full max-w-lg space-y-4 overflow-auto rounded-xl bg-white p-6"><h2 className="text-lg font-semibold">{editing ? 'Edit' : 'Add'} Faculty</h2>{fields.map(([key, label]) => <label key={key} className="block text-sm">{label}{key === 'department' ? <select required className="h-9 w-full rounded border px-2" value={String(form[key] || '')} onChange={(e) => setForm({ ...form, [key]: e.target.value })}><option value="">Select department</option>{departments.map((d) => <option key={d.id} value={d.id}>{d.name || d.code}</option>)}</select> : <Input required={key !== 'email'} value={String(form[key] || '')} type={key === 'email' ? 'email' : 'text'} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />}</label>)}<div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => { setEditing(null); setForm({}); }}>Cancel</Button><Button type="submit">Save</Button></div></form></div>}</main></AdminLayout>;
}
