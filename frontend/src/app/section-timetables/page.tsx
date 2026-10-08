'use client';

/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Printer, Search, Share2, UserRoundPen } from 'lucide-react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Button } from '@/components/ui/button';
import { apiClient } from '@/lib/api/client';
import { me } from '@/lib/api/auth';
import { OfficialSectionTimetable, type OfficialTimetableData } from '@/components/OfficialSectionTimetable';

type Choice = { id: string; name: string; code?: string; type?: string };
type VersionInfo = { id: string; version_no: number; status: string; title: string; published_at?: string | null };
type SectionRow = {
  id: string; name: string; year: number; program: Choice; department: Choice; semester: Choice; session: Choice;
  coordinator: { name: string; mobile: string; user_id?: string | null };
  version: VersionInfo | null; versions: VersionInfo[];
};
type Detail = OfficialTimetableData & { section: OfficialTimetableData['section'] & { id: string; student_strength: number }; versions: VersionInfo[] };
const statusLabel = (value?: string) => value?.replaceAll('_', ' ') ?? 'No published version';
const cleanFilename = (value: string) => value.normalize('NFKD').replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '');

export default function SectionTimetablesPage() {
  const [sections, setSections] = useState<SectionRow[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState({ session: '', semester: '', department: '', program: '', year: '', status: '' });
  const [coordinatorEdit, setCoordinatorEdit] = useState(false);
  const [coordinatorName, setCoordinatorName] = useState('');
  const [coordinatorMobile, setCoordinatorMobile] = useState('');
  const [savingCoordinator, setSavingCoordinator] = useState(false);
  const [notice, setNotice] = useState('');
  const [role, setRole] = useState('');

  const loadSections = useCallback(async () => {
    setLoading(true); setError('');
    try { setSections((await apiClient.get<SectionRow[]>('/section-timetables/')).data); }
    catch (requestError) {
      const status = (requestError as { response?: { status?: number } }).response?.status;
      setError(status === 403 ? 'Your account needs an assigned department scope to view section timetables.' : 'Unable to load section timetables.');
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void loadSections(); me().then(user => setRole(user.role)).catch(() => setRole('')); }, [loadSections]);

  const openSection = useCallback(async (id: string, requestedVersion = '') => {
    setSelectedId(id); setDetailLoading(true); setError(''); setNotice('');
    try {
      const suffix = requestedVersion ? `?version=${encodeURIComponent(requestedVersion)}` : '';
      const payload = (await apiClient.get<Detail>(`/sections/${id}/timetable/${suffix}`)).data;
      setDetail(payload); setCoordinatorName(payload.section.coordinator.name ?? ''); setCoordinatorMobile(payload.section.coordinator.mobile ?? '');
    } catch (requestError) {
      const status = (requestError as { response?: { status?: number } }).response?.status;
      setDetail(null); setError(status === 403 ? 'This section is outside your authorized department scope.' : 'No timetable is available for this section in the selected version.');
    } finally { setDetailLoading(false); }
  }, []);

  const options = useMemo(() => ({
    session: unique(sections.map(row => row.session)), semester: unique(sections.map(row => row.semester)),
    department: unique(sections.map(row => row.department)), program: unique(sections.map(row => row.program)),
  }), [sections]);
  const filtered = useMemo(() => sections.filter(section => {
    const q = search.trim().toLowerCase();
    const match = !q || [section.name, section.program.name, section.program.code ?? '', section.department.name, `year ${section.year}`].some(value => value.toLowerCase().includes(q));
    return match && (!filters.session || section.session.id === filters.session) && (!filters.semester || section.semester.id === filters.semester)
      && (!filters.department || section.department.id === filters.department) && (!filters.program || section.program.id === filters.program)
      && (!filters.year || String(section.year) === filters.year) && (!filters.status || section.version?.status === filters.status);
  }), [sections, search, filters]);

  const saveCoordinator = async () => {
    const digits = coordinatorMobile.replace(/\D/g, '');
    if (coordinatorMobile && (digits.length < 7 || digits.length > 15)) { setNotice('Enter a valid phone number with 7-15 digits.'); return; }
    setSavingCoordinator(true);
    try {
      const response = await apiClient.patch<{ coordinator: SectionRow['coordinator'] }>(`/sections/${selectedId}/coordinator/`, { name: coordinatorName.trim(), mobile: coordinatorMobile.trim() });
      if (detail) setDetail({ ...detail, section: { ...detail.section, coordinator: response.data.coordinator } });
      setCoordinatorEdit(false); setNotice('Coordinator details saved.'); await loadSections();
    } catch { setNotice('Coordinator details could not be saved. Check your access and the mobile number.'); }
    finally { setSavingCoordinator(false); }
  };

  const downloadPdf = async (row?: SectionRow) => {
    const target = row ?? detail?.section;
    if (!target) return;
    const version = detail?.section.id === target.id ? detail.version?.id : (target as SectionRow).version?.id;
    if (!version) { setNotice('No timetable version is available to download for this section.'); return; }
    try {
      const response = await apiClient.get<Blob>(`/sections/${target.id}/timetable/pdf/?version=${encodeURIComponent(version)}`, { responseType: 'blob' });
      const url = URL.createObjectURL(response.data); const anchor = document.createElement('a'); anchor.href = url;
      anchor.download = cleanFilename(`BBDU_${target.name}_${target.semester.name}_${target.session.name}_Timetable.pdf`); anchor.click(); URL.revokeObjectURL(url);
    } catch { setNotice('The timetable PDF could not be downloaded.'); }
  };
  const shareTimetable = async () => {
    if (!detail?.version) { setNotice('Select an available timetable version before sharing.'); return; }
    try {
      const response = await apiClient.get<Blob>(`/sections/${detail.section.id}/timetable/pdf/?version=${encodeURIComponent(detail.version.id)}`, { responseType: 'blob' });
      const filename = cleanFilename(`BBDU_${detail.section.name}_${detail.section.semester.name}_${detail.section.session.name}_Timetable.pdf`);
      const file = new File([response.data], filename, { type: 'application/pdf' });
      if (navigator.share && navigator.canShare?.({ files: [file] })) await navigator.share({ title: `${detail.section.name} timetable`, text: `${detail.section.program.name} | ${detail.section.session.name}`, url: window.location.href, files: [file] });
      else { await navigator.clipboard.writeText(window.location.href); setNotice('Timetable link copied.'); }
    } catch { setNotice('Timetable could not be shared.'); }
  };

  return <AdminLayout><Header title="Section Timetables"/><main className="section-timetable-page p-5 sm:p-8">
    <div className="section-no-print mb-5 flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight text-slate-900">Section Timetables</h1><p className="mt-1 text-sm text-slate-500">View, manage, print and download section-wise academic timetables.</p></div>
      {detail && <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => window.print()}><Printer className="mr-2 h-4 w-4"/>Print</Button><Button variant="outline" onClick={() => void downloadPdf()}><Download className="mr-2 h-4 w-4"/>Download PDF</Button><Button variant="outline" onClick={() => void shareTimetable()}><Share2 className="mr-2 h-4 w-4"/>Share</Button></div>}
    </div>
    {notice && <p role="status" className="section-no-print mb-4 rounded-md border bg-white px-4 py-3 text-sm">{notice}</p>}
    {error && <p role="alert" className="section-no-print mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
    {!detail && <section className="section-no-print rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b p-4 sm:p-5"><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Search Section<div className="relative mt-1"><Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400"/><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Section, program, year or department" className="w-full rounded-md border py-2 pl-9 pr-3 text-sm normal-case tracking-normal text-slate-900"/></div></label>
        <Filter label="Academic Session" value={filters.session} options={options.session} onChange={value => setFilters({ ...filters, session: value })}/>
        <Filter label="Semester" value={filters.semester} options={options.semester} onChange={value => setFilters({ ...filters, semester: value })}/>
        <Filter label="Department" value={filters.department} options={options.department} onChange={value => setFilters({ ...filters, department: value })}/>
        <Filter label="Program" value={filters.program} options={options.program} onChange={value => setFilters({ ...filters, program: value })}/>
        <Filter label="Year" value={filters.year} options={[1,2,3,4].map(year => ({ id: String(year), name: `Year ${year}` }))} onChange={value => setFilters({ ...filters, year: value })}/>
        <Filter label="Version Status" value={filters.status} options={['DRAFT','IN_REVIEW','APPROVED','PUBLISHED'].map(status => ({ id: status, name: statusLabel(status) }))} onChange={value => setFilters({ ...filters, status: value })}/>
      </div></div>
      {loading ? <p className="p-8 text-center text-sm text-slate-500">Loading permitted sections...</p> : !filtered.length ? <p className="p-10 text-center text-sm text-slate-500">No sections match the selected filters.</p> : <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr>{['Section','Program / Year','Session / Semester','Department','Coordinator','Version',''].map(title => <th key={title} className="px-4 py-3 font-semibold">{title}</th>)}</tr></thead><tbody>{filtered.map(row => <tr key={row.id} className="border-t hover:bg-slate-50/70"><td className="px-4 py-3 font-semibold">{row.name}</td><td className="px-4 py-3">{row.program.name}<span className="block text-xs text-slate-500">Year {row.year}</span></td><td className="px-4 py-3">{row.session.name}<span className="block text-xs text-slate-500">{row.semester.name}</span></td><td className="px-4 py-3">{row.department.name}</td><td className="px-4 py-3">{row.coordinator.name || 'Not assigned'}<span className="block text-xs text-slate-500">{row.coordinator.mobile}</span></td><td className="px-4 py-3">{statusLabel(row.version?.status)}{row.version && <span className="ml-1 text-xs text-slate-500">v{row.version.version_no}</span>}</td><td className="whitespace-nowrap px-4 py-3"><Button size="sm" onClick={() => void openSection(row.id)}>View</Button><Button size="sm" variant="ghost" aria-label={`Download ${row.name} PDF`} onClick={() => void downloadPdf(row)}><Download className="h-4 w-4"/></Button></td></tr>)}</tbody></table></div>}
      <div className="border-t px-4 py-3 text-xs text-slate-500">{filtered.length} section{filtered.length === 1 ? '' : 's'} in your authorized scope</div>
    </section>}
    {detailLoading && <p className="section-no-print rounded-lg border bg-white p-8 text-center text-sm text-slate-500">Loading section timetable...</p>}
    {detail && !detailLoading && <>
      <div className="section-no-print mb-4 flex flex-wrap items-center gap-3 rounded-lg border bg-white p-3">
        <label className="text-sm font-medium">Timetable Version<select className="ml-2 rounded-md border bg-white px-3 py-2 text-sm" value={detail.version?.id ?? ''} onChange={event => void openSection(selectedId, event.target.value)}><option value="">Published version unavailable</option>{detail.versions.map(version => <option key={version.id} value={version.id}>v{version.version_no} | {statusLabel(version.status)}</option>)}</select></label>
        {['SUPER_ADMIN', 'HOD_OR_DEAN_APPROVER'].includes(role) && <Button variant="outline" size="sm" onClick={() => setCoordinatorEdit(value => !value)}><UserRoundPen className="mr-2 h-4 w-4"/>Edit Coordinator</Button>}
        <Button className="ml-auto" variant="ghost" size="sm" onClick={() => { setDetail(null); setSelectedId(''); }}>Back to Sections</Button>
      </div>
      {coordinatorEdit && <div className="section-no-print mb-4 flex flex-wrap items-end gap-3 rounded-lg border bg-white p-4"><label className="text-sm font-medium">Coordinator Name<input maxLength={255} className="mt-1 block min-w-64 rounded-md border px-3 py-2" value={coordinatorName} onChange={event => setCoordinatorName(event.target.value)}/></label><label className="text-sm font-medium">Mobile Number<input className="mt-1 block min-w-52 rounded-md border px-3 py-2" value={coordinatorMobile} onChange={event => setCoordinatorMobile(event.target.value)} placeholder="10-digit or international"/></label><Button disabled={savingCoordinator} onClick={() => void saveCoordinator()}>{savingCoordinator ? 'Saving...' : 'Save'}</Button><Button variant="outline" onClick={() => setCoordinatorEdit(false)}>Cancel</Button></div>}
      {!detail.version ? <p className="section-no-print rounded border bg-white p-12 text-center text-sm">No timetable is available for this selected version.</p> : !detail.entries.length ? <p className="section-no-print rounded border bg-white p-12 text-center text-sm">No entries are available in Version {detail.version.version_no}.</p> : <div className="section-timetable-print-root overflow-x-auto rounded-xl border border-slate-300 bg-white p-3 shadow-sm sm:p-5"><OfficialSectionTimetable data={detail}/></div>}
    </>}
  </main></AdminLayout>;
}

function unique(values: Choice[]) { return [...new Map(values.map(value => [value.id, value])).values()].sort((a, b) => a.name.localeCompare(b.name)); }
function Filter({ label, value, options, onChange }: { label: string; value: string; options: Choice[]; onChange: (value: string) => void }) {
  return <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}<select value={value} onChange={event => onChange(event.target.value)} className="mt-1 block w-full rounded-md border bg-white px-3 py-2 text-sm font-normal normal-case tracking-normal text-slate-900"><option value="">All</option>{options.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select></label>;
}
