'use client';
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react';
import { Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { PageHeader } from '@/components/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { me } from '@/lib/api/auth';
import { create, deactivate, list, listAll, update, type Entity } from '@/lib/api/resources';
import { ImportExportActions } from '@/components/ImportExportActions';
import { canCreate, canEdit, canDeactivate, canImport, type Role } from '@/lib/permissions';
import { apiClient } from '@/lib/api/client';

type Field = { key: string; label: string; type?: string; placeholder?: string; required?: boolean; min?: number; relation?: { endpoint: string; label: string; display?: string[]; optionsEndpoint?: string; searchable?: boolean } };
type ApiError = { response?: { data?: Record<string, unknown> } };

export function CrudPage({ title, description, endpoint, fields, columns = fields.slice(0, 4), loadAllRecords = false }: { title: string; description: string; endpoint: string; fields: Field[]; columns?: Field[]; loadAllRecords?: boolean }) {
  const [items, setItems] = useState<Entity[]>([]), [count, setCount] = useState(0), [search, setSearch] = useState(''), [loading, setLoading] = useState(true), [error, setError] = useState(''), [editing, setEditing] = useState<Entity | null>(null), [form, setForm] = useState<Record<string, unknown>>({}), [dialogOpen, setDialogOpen] = useState(false), [options, setOptions] = useState<Record<string, Entity[]>>({}), [relationSearch, setRelationSearch] = useState<Record<string, string>>({}), [role, setRole] = useState<Role>();
  const load = () => {
    setLoading(true);
    const request = loadAllRecords ? listAll<Entity>(endpoint, search ? { search } : {}).then(all => { setItems(all); setCount(all.length); }) : list<Entity>(endpoint, search ? { search } : {}).then(page => { setItems(page.results); setCount(page.count); });
    request.catch(() => setError('Could not load records.')).finally(() => setLoading(false));
  };
  useEffect(load, [endpoint, search, loadAllRecords]);
  useEffect(() => {
    me().then(user => setRole(user.role as Role)).catch(() => setRole(undefined));
    Promise.all(fields.filter(field => field.relation).map(async field => {
      const relation = field.relation!;
      if (relation.optionsEndpoint) { const { data } = await apiClient.get<Entity[]>(`/${relation.optionsEndpoint}/`); return [field.key, data] as const; }
      return [field.key, (await list<Entity>(relation.endpoint)).results] as const;
    })).then(values => setOptions(Object.fromEntries(values))).catch(() => undefined);
  }, [fields]);
  const close = () => { setDialogOpen(false); setEditing(null); setForm({}); };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError('');
    try { if (editing) await update(endpoint, editing.id, form); else await create(endpoint, form); close(); load(); }
    catch (caught) { const data = (caught as ApiError).response?.data; setError(data ? Object.entries(data).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`).join(' | ') : 'Save failed.'); setDialogOpen(true); }
  };
  const writable = canCreate(role);
  const display = (item: Entity, field: Field) => {
    const expanded = item[`${field.key}_name`]; if (typeof expanded === 'string' && expanded) return expanded;
    const value = item[field.key];
    if (field.relation) { const found = (options[field.key] ?? []).find(option => option.id === value); if (found) return optionLabel(found, field); }
    return String(value ?? '—');
  };
  const optionLabel = (option: Entity, field: Field) => field.relation?.display?.map(key => String(option[key] ?? '')).filter(Boolean).join(' \u2014 ') || String(option[field.relation?.label ?? 'name'] ?? option.name ?? option.code ?? option.id);
  const hasImportExport = ['sections', 'course-offerings'].includes(endpoint);
  return <AdminLayout><Header title={title}/><main className="mx-auto w-full max-w-[1500px] p-5 sm:p-8"><PageHeader title={title} description={description} extraAction={hasImportExport ? <ImportExportActions kind={endpoint} endpoint={endpoint} canImport={canImport(role)} onComplete={load} params={{ search: search || undefined }} /> : undefined}/>
    <div className="mb-4 flex flex-wrap gap-2"><div className="relative max-w-sm flex-1"><Search className="absolute left-2 top-2.5 h-4 w-4 text-slate-400"/><Input className="pl-8" placeholder="Search" value={search} onChange={event => setSearch(event.target.value)}/></div>
      {writable && <Button onClick={() => { setEditing(null); setForm({}); setRelationSearch({}); setError(''); setDialogOpen(true); }}><Plus className="mr-2 h-4 w-4"/>Add</Button>}
    </div>
    {error && <p className="mb-3 rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <Card><CardContent className="p-0">{loading ? <p className="p-8 text-sm text-slate-500">Loading...</p> : items.length === 0 ? <p className="p-8 text-sm text-slate-500">{writable ? `No ${title.toLowerCase()} have been added yet.` : `No ${title.toLowerCase()} records are currently available.`}</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b bg-slate-50"><tr>{columns.map(field => <th key={field.key} className="p-3">{field.label}</th>)}<th className="p-3">Actions</th></tr></thead><tbody>{items.map(item => <tr key={item.id} className="border-b">{columns.map(field => <td key={field.key} className="p-3">{display(item, field)}</td>)}<td className="p-3">{canEdit(role) && <Button variant="ghost" size="sm" onClick={() => { setEditing(item); setForm(Object.fromEntries(fields.map(field => [field.key, item[field.key] ?? '']))); setRelationSearch({}); setOptions(current => ({ ...current, course: [...(current.course ?? []).filter(option => option.id !== item.course), ...(item.course ? [{ id: String(item.course), code: item.course_code, name: item.course_name } as Entity] : [])] })); setError(''); setDialogOpen(true); }}><Pencil className="h-4 w-4"/></Button>}{canDeactivate(role) && <Button variant="ghost" size="sm" onClick={() => { if (confirm('Deactivate this record?')) deactivate(endpoint, item.id).then(load).catch(() => setError('Action failed.')); }}><Trash2 className="h-4 w-4"/></Button>}</td></tr>)}</tbody></table></div>}<p className="border-t p-3 text-xs text-slate-500">{count} total records</p></CardContent></Card>
    {dialogOpen && <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4"><form onSubmit={submit} className="max-h-[90vh] w-full max-w-lg space-y-4 overflow-auto rounded-xl bg-white p-6"><h2 className="text-lg font-semibold">{editing ? 'Edit' : 'Add'} {title}</h2>{fields.map(field => <label key={field.key} className="block text-sm"><span className="mb-1 block">{field.label}</span>{field.relation ? <>{field.relation.searchable && <Input className="mb-1" placeholder={`Search ${field.label.toLowerCase()} by code or name`} value={relationSearch[field.key] ?? ''} onChange={event => setRelationSearch({ ...relationSearch, [field.key]: event.target.value })}/>}<select required={field.required} className="h-9 w-full rounded border px-2" value={String(form[field.key] ?? '')} onChange={event => setForm({ ...form, [field.key]: event.target.value })}><option value="">Select {field.label}</option>{(options[field.key] ?? []).filter(option => !field.relation!.searchable || optionLabel(option, field).toLowerCase().includes((relationSearch[field.key] ?? '').toLowerCase())).map(option => <option key={option.id} value={option.id}>{optionLabel(option, field)}</option>)}</select></> : <Input required={field.required} min={field.min} type={field.type ?? 'text'} placeholder={field.placeholder} value={String(form[field.key] ?? '')} onChange={event => setForm({ ...form, [field.key]: field.type === 'number' ? Number(event.target.value) : event.target.value })}/>}</label>)}<div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={close}>Cancel</Button><Button type="submit">Save</Button></div></form></div>}
  </main></AdminLayout>;
}
