'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { apiClient } from '@/lib/api/client';
import { me } from '@/lib/api/auth';
import { listAll } from '@/lib/api/resources';

type Faculty = { id: string; name?: string; initials?: string; employee_code?: string };
type OwnClass = { schedule_entry: string; weekday: number; day: string; time_slot: { label: string; start_time: string; end_time: string }; course: { code: string; name: string }; section: { id: string; name: string }; room: string | null; delivery_mode: string; period_count: number; original_faculty: string };
type Row = Record<string, string | number | boolean | null | undefined> & { id: string; evidence?: { id: string; uploaded_at?: string; original_filename?: string } | null };
type Affected = Row & { schedule_entry: string; candidates: Faculty[] };
const label = (faculty: Faculty) => faculty.name || faculty.initials || faculty.employee_code || 'Unnamed faculty';
const attendanceClassEnd = (row: Row) => typeof row.class_end_at === 'string' ? Date.parse(row.class_end_at) : Number.NaN;
const attendanceReady = (row: Row, now: number) => row.is_arrangement_faculty === true && row.status === 'ASSIGNED' && row.attendance === 'Pending' && Number.isFinite(attendanceClassEnd(row)) && now >= attendanceClassEnd(row);
const attendanceAvailableLabel = (row: Row) => {
  if (typeof row.class_end_at !== 'string') return 'Class end time unavailable.';
  const end = new Date(row.class_end_at);
  if (!Number.isFinite(end.getTime())) return 'Class end time unavailable.';
  const zone = typeof row.timezone === 'string' ? row.timezone : undefined;
  const formatted = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true, timeZone: zone }).format(end).replace(/\b(am|pm)\b/g, value => value.toUpperCase());
  return `Available after ${formatted}`;
};
const apiMessage = (error: unknown, fallback: string) => {
  if (typeof error === 'object' && error && 'response' in error) {
    const response = (error as { response?: { data?: unknown } }).response;
    if (typeof response?.data === 'string') return response.data;
    if (typeof response?.data === 'object' && response.data && 'detail' in response.data && typeof response.data.detail === 'string') return response.data.detail;
  }
  return fallback;
};
const arrangementManagerRoles = ['SUPER_ADMIN', 'ACADEMIC_ADMIN', 'TIMETABLE_COORDINATOR', 'HOD_OR_DEAN_APPROVER'];

export default function Page() {
  const [rows, setRows] = useState<Row[]>([]);
  const [faculty, setFaculty] = useState<Faculty[]>([]);
  const [affected, setAffected] = useState<Affected[]>([]);
  const [subs, setSubs] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [role, setRole] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [date, setDate] = useState('');
  const [absent, setAbsent] = useState('');
  const [search, setSearch] = useState('');
  const [message, setMessage] = useState('');
  const [finding, setFinding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadRow, setUploadRow] = useState<Row | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dialog, setDialog] = useState<Row | null>(null);
  const [options, setOptions] = useState<Faculty[]>([]);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [reassigning, setReassigning] = useState(false);
  const [selfCreateOpen, setSelfCreateOpen] = useState(false);
  const [myClasses, setMyClasses] = useState<OwnClass[]>([]);
  const [myClassId, setMyClassId] = useState('');
  const [myDate, setMyDate] = useState('');
  const [myCandidates, setMyCandidates] = useState<Faculty[]>([]);
  const [mySubstitute, setMySubstitute] = useState('');
  const [myClassesLoading, setMyClassesLoading] = useState(false);
  const [availabilityState, setAvailabilityState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle');
  const [availabilityMessage, setAvailabilityMessage] = useState('');
  const [availabilityRefresh, setAvailabilityRefresh] = useState(0);
  const [mySaving, setMySaving] = useState(false);
  const [currentDateTime, setCurrentDateTime] = useState(() => Date.now());
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const availabilityController = useRef<AbortController | null>(null);

  const load = () => Promise.all([
    apiClient.get('/faculty-arrangements/'),
    listAll<Faculty>('faculty'),
    apiClient.get('/faculty-arrangements/summary/'),
    me(),
  ]).then(([arrangements, allFaculty, counts, user]) => {
    setRows(arrangements.data);
    setFaculty([...allFaculty].sort((a, b) => label(a).localeCompare(label(b))));
    setSummary(counts.data);
    setRole(user.role);
  }).catch(() => setMessage('Unable to load faculty arrangements.'));

  useEffect(() => {
    void load();
    return () => controller.current?.abort();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setCurrentDateTime(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  const resetSelection = (nextDate: string, nextAbsent: string) => {
    setDate(nextDate);
    setAbsent(nextAbsent);
    setAffected([]);
    setSubs({});
    setMessage('');
  };

  const find = async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const token = ++sequence.current;
    setFinding(true);
    setMessage('');
    setAffected([]);
    setSubs({});
    try {
      const response = await apiClient.get('/faculty-arrangements/affected-classes/', {
        params: { date, absent_faculty: absent }, signal: current.signal,
      });
      if (current.signal.aborted || token !== sequence.current) return;
      setAffected(response.data);
      if (!response.data.length) setMessage('No published classes found for this faculty on the selected date.');
    } catch (err: unknown) {
      if (!current.signal.aborted) setMessage(apiMessage(err, 'Unable to load affected classes.'));
    } finally {
      if (!current.signal.aborted && token === sequence.current) setFinding(false);
    }
  };

  const saveAll = async () => {
    setSaving(true);
    try {
      await apiClient.post('/faculty-arrangements/bulk/', {
        date, absent_faculty: absent,
        arrangements: affected.map(row => ({ schedule_entry: row.schedule_entry, substitute_faculty: subs[row.schedule_entry] })),
      });
      setMessage('Arrangements saved successfully.');
      setAffected([]);
      setSubs({});
      await load();
    } catch (err: unknown) {
      setMessage(apiMessage(err, 'No arrangements were saved.'));
    } finally {
      setSaving(false);
    }
  };

  const openReassign = async (row: Row) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const token = ++sequence.current;
    setDialog(row);
    setOptions([]);
    setSelected('');
    setError('');
    setLoading(true);
    try {
      const response = await apiClient.get('/faculty-arrangements/available-faculty/', {
        params: { date: row.arrangement_date, schedule_entry: row.schedule_entry }, signal: current.signal,
      });
      if (!current.signal.aborted && token === sequence.current) {
        setOptions(response.data.filter((item: Faculty) => item.id !== row.substitute_faculty_id && item.id !== row.absent_faculty_id));
      }
    } catch (err: unknown) {
      if (!current.signal.aborted) setError(apiMessage(err, 'Unable to load available faculty.'));
    } finally {
      if (!current.signal.aborted && token === sequence.current) setLoading(false);
    }
  };

  const close = () => {
    controller.current?.abort();
    setDialog(null);
    setOptions([]);
    setSelected('');
    setError('');
  };

  const reassign = async () => {
    if (!dialog || !selected || reassigning) return;
    setReassigning(true);
    try {
      await apiClient.patch(`/faculty-arrangements/${dialog.id}/reassign/`, { substitute_faculty: selected });
      close();
      setMessage('Arrangement reassigned successfully.');
      await load();
    } catch (err: unknown) {
      setError(apiMessage(err, 'Unable to reassign arrangement.'));
    } finally {
      setReassigning(false);
    }
  };

  const cancel = async (row: Row) => {
    if (!window.confirm(`Cancel this faculty arrangement?\n${row.course_code} · ${row.arrangement_date}`)) return;
    try {
      await apiClient.post(`/faculty-arrangements/${row.id}/cancel/`);
      setMessage('Arrangement cancelled successfully.');
      await load();
    } catch (err: unknown) {
      setMessage(apiMessage(err, 'Unable to cancel arrangement.'));
    }
  };

  const openSelfCreate = async () => {
    setSelfCreateOpen(true); setMyClasses([]); setMyClassId(''); setMyDate(''); setMyCandidates([]); setMySubstitute(''); setAvailabilityState('idle'); setAvailabilityMessage(''); setMessage(''); setMyClassesLoading(true);
    try {
      const response = await apiClient.get<OwnClass[]>('/faculty-arrangements/my-classes/');
      setMyClasses(response.data);
    } catch (err: unknown) { setMessage(apiMessage(err, 'Unable to load your published classes.')); }
    finally { setMyClassesLoading(false); }
  };

  useEffect(() => {
    availabilityController.current?.abort();
    if (!selfCreateOpen || !myClassId || !myDate) {
      return;
    }

    const selectedClass = myClasses.find(item => item.schedule_entry === myClassId);
    if (!selectedClass || myClassesLoading) return;

    const [year, month, day] = myDate.split('-').map(Number);
    const chosen = new Date(year, month - 1, day);
    if (chosen.getDay() !== (selectedClass.weekday + 1) % 7) return;

    const requestController = new AbortController();
    availabilityController.current = requestController;
    void (async () => {
      await Promise.resolve();
      if (requestController.signal.aborted) return;
      setAvailabilityMessage('');
      setAvailabilityState('loading');
      try {
        const response = await apiClient.get<Faculty[]>('/faculty-arrangements/available-faculty/', {
          params: { schedule_entry: myClassId, date: myDate },
          signal: requestController.signal,
        });
        if (requestController.signal.aborted) return;
        const results = Array.isArray(response.data) ? response.data : [];
        setMyCandidates(results);
        setAvailabilityState('loaded');
        if (!results.length) setAvailabilityMessage('No faculty available for this date and time.');
      } catch (err: unknown) {
        if (requestController.signal.aborted) return;
        setAvailabilityState('error');
        setAvailabilityMessage(apiMessage(err, 'Unable to load available faculty.'));
      }
    })();
    return () => requestController.abort();
  }, [selfCreateOpen, myClassId, myDate, myClasses, myClassesLoading, availabilityRefresh]);

  const findMyCandidates = () => {
    setMyCandidates([]);
    setMySubstitute('');
    setAvailabilityMessage('');
    setAvailabilityState('loading');
    setAvailabilityRefresh(value => value + 1);
  };

  const saveMyArrangement = async () => {
    if (!myClassId || !myDate || !mySubstitute || mySaving || availabilityState !== 'loaded' || !myCandidates.some(item => item.id === mySubstitute)) return;
    setMySaving(true); setMessage('');
    try {
      await apiClient.post('/faculty-arrangements/', { schedule_entry: myClassId, arrangement_date: myDate, substitute_faculty: mySubstitute });
      setSelfCreateOpen(false); setMessage('Faculty arrangement created successfully.');
      await load();
    } catch (err: unknown) { setMessage(apiMessage(err, 'Unable to create the faculty arrangement.')); }
    finally { setMySaving(false); }
  };

  const upload = async () => {
    if (!uploadRow || !file || uploading) return;
    if (!['image/jpeg', 'image/png', 'image/webp', 'application/pdf'].includes(file.type) || file.size > 10 * 1024 * 1024) {
      setMessage('Use JPG, JPEG, PNG, WEBP, or PDF up to 10 MB.');
      return;
    }
    setUploading(true);
    try {
      const body = new FormData();
      body.append('file', file);
      await apiClient.post(`/faculty-arrangements/${uploadRow.id}/attendance/`, body);
      setUploadRow(null);
      setFile(null);
      setMessage('Attendance uploaded successfully.');
      await load();
    } catch (err: unknown) {
      setMessage(apiMessage(err, 'Unable to upload attendance evidence.'));
    } finally {
      setUploading(false);
    }
  };

  const coordinator = role === 'TIMETABLE_COORDINATOR';
  const canCreateArrangement = arrangementManagerRoles.includes(role);
  const facultyUser = role === 'FACULTY';
  const myLoading = myClassesLoading || availabilityState === 'loading';
  const selectedMyClass = myClasses.find(item => item.schedule_entry === myClassId);
  const dateParts = myDate.split('-').map(Number);
  const selectedDateWeekday = myDate && dateParts.length === 3 ? new Date(dateParts[0], dateParts[1] - 1, dateParts[2]).getDay() : null;
  const dateMismatch = !!selectedMyClass && selectedDateWeekday !== null && selectedDateWeekday !== (selectedMyClass.weekday + 1) % 7;
  const dateMismatchMessage = dateMismatch ? `Selected date must be a ${selectedMyClass.day} for this class.` : '';
  const visible = faculty.filter(item => `${label(item)} ${item.employee_code || ''}`.toLowerCase().includes(search.toLowerCase()));

  return <AdminLayout>
    <Header title="Faculty Arrangements" />
    <main className="space-y-5 p-5 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Faculty Arrangements</h1>
        {facultyUser && <button className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-800" onClick={() => void openSelfCreate()}>Arrange My Class</button>}
        {canCreateArrangement && <button className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-800" onClick={() => {
          setCreateOpen(true);
          document.getElementById('create-faculty-arrangement')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }}>Create Arrangement</button>}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {([['Today', summary.today], ['This Week', summary.this_week], ['This Month', summary.this_month], ['Pending Attendance', summary.pending_evidence]] as const).map(([title, count]) => <Card key={title}><CardContent className="p-4"><p className="text-xs text-slate-500">{title}</p><p className="text-2xl font-semibold">{count ?? 0}</p></CardContent></Card>)}
      </div>

      {canCreateArrangement && (coordinator || createOpen) && <Card id="create-faculty-arrangement">
        <CardHeader><CardTitle>Find affected classes</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <label>Date<input type="date" className="mt-1 block rounded border p-2" value={date} onChange={event => resetSelection(event.target.value, '')} /></label>
          <label>Search faculty<input className="mt-1 block rounded border p-2" placeholder="Name or employee code" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <label>Absent Faculty<select className="mt-1 block min-w-64 rounded border p-2" value={absent} onChange={event => resetSelection(date, event.target.value)}><option value="">Select faculty</option>{visible.map(item => <option key={item.id} value={item.id}>{label(item)} — {item.employee_code}</option>)}</select></label>
          <button disabled={!date || !absent || finding} className="rounded bg-slate-900 px-4 py-2 text-white" onClick={() => void find()}>{finding ? 'Finding classes...' : 'Find Classes'}</button>
        </CardContent>
      </Card>}

      {message && <p className="rounded bg-slate-100 p-3 text-sm">{message}</p>}
      {selfCreateOpen && facultyUser && <Card><CardHeader><CardTitle>Arrange My Class</CardTitle></CardHeader><CardContent className="space-y-4">
        {myClassesLoading && <p className="text-sm text-slate-500">Loading your published classes...</p>}
        {!myClassesLoading && !myClasses.length && <p className="text-sm text-slate-500">No classes were found in your active published timetable.</p>}
        {availabilityState === 'loading' && <p className="text-sm text-slate-600" role="status">Checking available faculty...</p>}
        {dateMismatchMessage && <p className="text-sm text-red-700" role="alert">{dateMismatchMessage}</p>}
        {availabilityMessage && <p className={`text-sm ${availabilityState === 'error' ? 'text-red-700' : 'text-slate-600'}`} role={availabilityState === 'error' ? 'alert' : 'status'}>{availabilityMessage}</p>}
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-sm">My published class<select className="mt-1 block w-full rounded border p-2" value={myClassId} onChange={event => { setMyClassId(event.target.value); setMyCandidates([]); setMySubstitute(''); }}><option value="">Select your class</option>{myClasses.map(item => <option key={item.schedule_entry} value={item.schedule_entry}>{item.day} {item.time_slot.label} · {item.course.code} · {item.section.name} · {item.delivery_mode === 'ONLINE' ? 'Online' : item.room || 'Room pending'} ({item.period_count} period{item.period_count === 1 ? '' : 's'})</option>)}</select></label>
          <label className="text-sm">Replacement date<input type="date" min={new Date().toISOString().slice(0, 10)} className="mt-1 block w-full rounded border p-2" value={myDate} onChange={event => { setMyDate(event.target.value); setMyCandidates([]); setMySubstitute(''); }} /></label>
          <div className="text-sm"><span className="block">Original Faculty</span><p className="mt-1 rounded bg-slate-50 p-2">{myClasses.find(item => item.schedule_entry === myClassId)?.original_faculty || 'Select your class'}</p></div>
        </div>
        <div className="flex flex-wrap gap-2"><button disabled={!myClassId || !myDate || myLoading} className="rounded border px-3 py-2 disabled:opacity-50" onClick={() => void findMyCandidates()}>{myLoading ? 'Checking...' : 'Find Available Faculty'}</button><select aria-label="Available replacement faculty" disabled={!myCandidates.length} className="min-w-64 rounded border p-2" value={mySubstitute} onChange={event => setMySubstitute(event.target.value)}><option value="">Select available replacement</option>{myCandidates.map(item => <option key={item.id} value={item.id}>{label(item)} · {item.employee_code}</option>)}</select><button disabled={!mySubstitute || mySaving} className="rounded bg-slate-900 px-3 py-2 text-white disabled:opacity-50" onClick={() => void saveMyArrangement()}>{mySaving ? 'Saving...' : 'Create Arrangement'}</button><button className="rounded border px-3 py-2" onClick={() => setSelfCreateOpen(false)}>Close</button></div>
      </CardContent></Card>}
      {canCreateArrangement && affected.length > 0 && <Card>
        <CardHeader><CardTitle>Affected Classes</CardTitle></CardHeader>
        <CardContent><div className="space-y-2">{affected.map(row => <div className="flex flex-wrap items-center gap-3 border-b p-2" key={row.schedule_entry}>
          <span>{row.time_slot} · {row.course_code} · {row.section} · Room: {row.room || '—'}</span>
          <select className="rounded border p-2" value={subs[row.schedule_entry] || ''} onChange={event => setSubs(current => ({ ...current, [row.schedule_entry]: event.target.value }))}>
            <option value="">Select eligible faculty</option>{row.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{label(candidate)} — {candidate.employee_code}</option>)}
          </select>
        </div>)}</div>
          <button disabled={saving || affected.some(row => !subs[row.schedule_entry])} className="mt-4 rounded bg-slate-900 px-4 py-2 text-white" onClick={() => void saveAll()}>{saving ? 'Saving arrangements...' : 'Save All Arrangements'}</button>
        </CardContent>
      </Card>}

      <Card><CardHeader><CardTitle>Arrangement History</CardTitle></CardHeader><CardContent className="overflow-x-auto">
        <table className="w-full min-w-[1000px] text-sm"><thead><tr className="border-b text-left"><th>Date</th><th>Time</th><th>Course</th><th>Section</th><th>Original Faculty</th><th>Arrangement Faculty</th><th>Status</th><th>Attendance</th><th>Actions</th></tr></thead>
          <tbody>{rows.map(row => <tr className="border-b" key={row.id}><td>{row.arrangement_date}</td><td>{row.time_slot}</td><td>{row.course_code}</td><td>{row.section}</td><td>{row.absent_faculty}</td><td>{row.substitute_faculty}</td><td>{row.status}</td><td>{row.attendance}</td><td>
            {coordinator && row.status === 'ASSIGNED' && <><button className="mr-2 rounded border px-2 py-1" onClick={() => void openReassign(row)}>Reassign</button><button className="rounded border px-2 py-1 text-red-700" onClick={() => void cancel(row)}>Cancel</button></>}
            {facultyUser && row.is_arrangement_faculty === true && row.status === 'ASSIGNED' && row.attendance === 'Pending' && (attendanceReady(row, currentDateTime) ? <button className="rounded border px-2 py-1" onClick={() => { setUploadRow(row); setFile(null); }}>Upload Attendance</button> : <div><button type="button" disabled title="Attendance can be uploaded only after the class ends." className="rounded border px-2 py-1 opacity-50">Upload Attendance</button><p className="mt-1 text-xs text-slate-500">{attendanceAvailableLabel(row)}</p></div>)}
            {facultyUser && row.attendance === 'Uploaded' && row.evidence?.id && <Link className="rounded border px-2 py-1 text-blue-700" href={`/faculty-arrangements/attendance/${row.id}/${row.evidence.id}`}>View Attendance</Link>}
          </td></tr>)}</tbody>
        </table>
      </CardContent></Card>

      {dialog && <div className="fixed inset-0 grid place-items-center bg-black/40 p-4"><Card className="w-full max-w-lg"><CardHeader><CardTitle>Reassign arrangement</CardTitle></CardHeader><CardContent className="space-y-3">
        <p>Current Faculty: {dialog.substitute_faculty}</p>{loading && <p>Loading available faculty...</p>}{error && <p className="text-sm text-red-700">{error}</p>}{!loading && !error && !options.length && <p>No eligible faculty is available for this class.</p>}
        {!loading && !error && options.length > 0 && <select className="w-full rounded border p-2" value={selected} onChange={event => setSelected(event.target.value)}><option value="">Select eligible faculty</option>{options.map(item => <option key={item.id} value={item.id}>{label(item)} — {item.employee_code}</option>)}</select>}
        <div className="flex justify-end gap-2"><button className="rounded border px-3 py-2" onClick={close}>Close</button><button disabled={loading || reassigning || !!error || !selected} className="rounded bg-slate-900 px-3 py-2 text-white" onClick={() => void reassign()}>{reassigning ? 'Reassigning...' : 'Save'}</button></div>
      </CardContent></Card></div>}

      {uploadRow && <div className="fixed inset-0 grid place-items-center bg-black/40 p-4"><Card className="w-full max-w-lg"><CardHeader><CardTitle>Upload Attendance</CardTitle></CardHeader><CardContent className="space-y-3">
        <p>Course: {uploadRow.course_code}</p><p>Section: {uploadRow.section}</p><p>Date: {uploadRow.arrangement_date} · {uploadRow.time_slot}</p><input type="file" accept=".jpg,.jpeg,.png,.webp,.pdf" onChange={event => setFile(event.target.files?.[0] || null)} />
        <div className="flex justify-end gap-2"><button className="rounded border px-3 py-2" onClick={() => setUploadRow(null)}>Close</button><button disabled={!file || uploading} className="rounded bg-slate-900 px-3 py-2 text-white" onClick={() => void upload()}>{uploading ? 'Uploading...' : 'Upload'}</button></div>
      </CardContent></Card></div>}
    </main>
  </AdminLayout>;
}
