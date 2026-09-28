'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { schedulingApi, Version } from '@/lib/api/scheduling';
import { me } from '@/lib/api/auth';
import { apiClient } from '@/lib/api/client';
import { canGenerateTimetable, Role } from '@/lib/permissions';

const reviewers = ['SUPER_ADMIN', 'ACADEMIC_ADMIN', 'HOD_OR_DEAN_APPROVER'];
const publishers = ['SUPER_ADMIN', 'ACADEMIC_ADMIN'];
const apiError = (error: unknown) => (error as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'The requested action failed.';

export default function Page() {
  const { id } = useParams<{ id: string }>();
  const [versions, setVersions] = useState<Version[]>([]);
  const [role, setRole] = useState<Role>();
  const [error, setError] = useState('');
  const load = async () => setVersions(await schedulingApi.listVersions(id));
  useEffect(() => {
    void (async () => {
      try {
        const [loadedVersions, user] = await Promise.all([schedulingApi.listVersions(id), me()]);
        setVersions(loadedVersions);
        setRole(user.role as Role);
      } catch {
        setError('Unable to load versions.');
      }
    })();
  }, [id]);
  const action = async (version: Version, path: string, body?: Record<string, string>) => { try { await apiClient.post(`/versions/${version.id}/${path}/`, body); await load(); } catch (requestError: unknown) { setError(apiError(requestError)); } };
  const submitVersion = async (version: Version) => {
    const url = `/versions/${version.id}/submit-for-approval/`;
    console.log('[VERSION SUBMIT]', { versionNumber: version.version_no, versionId: version.id, status: version.status, url });
    try { await apiClient.post(url); await load(); } catch (requestError: unknown) { setError(apiError(requestError)); }
  };
  const createDraft = async (version: Version) => { if (!window.confirm(`Create New Draft?\n\nA new editable draft will be created from Version ${version.version_no}. The published version will remain unchanged.`)) return; try { const response = await apiClient.post<Version>(`/versions/${version.id}/create-draft/`); await load(); window.alert(`Version ${response.data.version_no} draft created successfully.`); } catch (requestError: unknown) { setError(apiError(requestError)); } };
  return <AdminLayout><Header title="Timetable Versions" /><main className="p-5 sm:p-8"><div className="mb-4 flex items-center justify-between"><div><h1 className="text-2xl font-semibold">Versions</h1><p className="text-sm text-slate-500">Source versions and lifecycle history.</p></div><div className="flex gap-2">{canGenerateTimetable(role) && <Link href={`/timetables/${id}/generate`}><Button>Generate Timetable</Button></Link>}<Link href={`/timetables/${id}/compare`}><Button variant="outline">Compare Versions</Button></Link></div></div><Card><CardHeader><CardTitle>Version history</CardTitle></CardHeader><CardContent>{error && <p className="mb-3 rounded bg-red-50 p-3 text-red-700">{error}</p>}<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b">{['Version', 'Status', 'Created At', 'Entries', 'Submitted', 'Approved', 'Published', 'Actions'].map(label => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{versions.map(version => <tr key={version.id} className="border-b"><td className="p-3">v{version.version_no}</td><td className="p-3">{version.status.replace('_', ' ')}</td><td className="p-3">{new Date(version.created_at).toLocaleString()}</td><td className="p-3">{version.entry_count}</td><td className="p-3">{version.submitted_at ? 'Yes' : '—'}</td><td className="p-3">{version.approved_at ? 'Yes' : '—'}</td><td className="p-3">{version.published_at ? 'Yes' : '—'}</td><td className="flex flex-wrap gap-2 p-3"><Link href={`/timetables/${id}/builder?version=${version.id}`}><Button size="sm" variant="outline">{version.status === 'DRAFT' ? 'Open Builder' : 'View'}</Button></Link><Link href={`/timetables/${id}/compare?from=${version.id}`}><Button size="sm" variant="outline">Compare</Button></Link><Link href={`/timetables/${id}/versions/${version.id}/history`}><Button size="sm" variant="outline">History</Button></Link>{role && version.status === 'DRAFT' && canGenerateTimetable(role) && <Button size="sm" onClick={() => void submitVersion(version)}>Submit</Button>}{role && version.status === 'IN_REVIEW' && reviewers.includes(role) && <><Button size="sm" onClick={() => void action(version, 'approve')}>Approve</Button><Button size="sm" variant="outline" onClick={() => { const reason = window.prompt('Reason for rejection'); if (reason?.trim()) void action(version, 'reject', { reason }); }}>Reject</Button></>}{role && version.status === 'APPROVED' && publishers.includes(role) && <Button size="sm" onClick={() => void action(version, 'publish')}>Publish</Button>}{role && version.status === 'PUBLISHED' && canGenerateTimetable(role) && <Button size="sm" onClick={() => void createDraft(version)}>Create New Draft</Button>}</td></tr>)}</tbody></table></div></CardContent></Card></main></AdminLayout>;
}
