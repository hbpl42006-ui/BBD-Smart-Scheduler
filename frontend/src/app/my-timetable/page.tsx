'use client';
/* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { me } from '@/lib/api/auth';
import { apiClient } from '@/lib/api/client';
import { FacultyTimetableResponse, ScheduleEntry, TimeSlot, schedulingApi } from '@/lib/api/scheduling';
import { getEntryPeriodCount, getWorkloadCellSpan, WorkloadGridEntry, WorkloadGridSlot } from '@/lib/faculty-workload-grid';

const periods = ['09 to 10', '10 to 11', '11 to 12', '12 to 1', '1 to 2', '2 to 3', '3 to 4', '4 to 5'];
const days = [['Mon', 0], ['Tue', 1], ['Wed', 2], ['Thu', 3], ['Fri', 4]] as const;
const typeCode = (type: string) => type === 'LECTURE' ? 'L' : type === 'TUTORIAL' ? 'T' : type === 'PRACTICAL' || type === 'LAB' ? 'P' : type;
type Assignment = { id?: string; name?: string; initials?: string };
const assignments = (entry: ScheduleEntry) => (entry as ScheduleEntry & { faculty_assignments?: Assignment[] }).faculty_assignments ?? [];
const facultyCodes = (entry: ScheduleEntry) => assignments(entry).map(item => item.initials || item.name || '—').join('+') || '—';
const notation = (entry: ScheduleEntry) => `${typeCode(entry.entry_type)}/${entry.course?.code ?? entry.course_offering}/${facultyCodes(entry)}/${entry.delivery_mode === 'ONLINE' ? 'ONLINE' : entry.room_code ?? 'Room pending'}`;
const facultyNames = (allEntries: ScheduleEntry[], entry: ScheduleEntry) => { const key = entry.course?.code ?? entry.course_offering; const seen = new Set<string>(); const names = allEntries.filter(item => (item.course?.code ?? item.course_offering) === key).flatMap(assignments).map(item => item.name).filter((name): name is string => Boolean(name)); return names.filter(name => !seen.has(name) && (seen.add(name), true)).join(', ') || '—'; };
const roman = (value: number) => ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII'][value] ?? String(value);

export default function MyTimetablePage() {
  const router = useRouter();
  const [data, setData] = useState<FacultyTimetableResponse>({} as FacultyTimetableResponse);
  const [slots, setSlots] = useState<TimeSlot[]>([]);
  const [entries, setEntries] = useState<ScheduleEntry[]>([]);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(true);
  const [sectionLoading, setSectionLoading] = useState(false);
  const [error, setError] = useState('');
  const [coordinator, setCoordinator] = useState('');
  const [mobile, setMobile] = useState('');
  const [editing, setEditing] = useState(false);
  useEffect(() => { setCoordinator(localStorage.getItem('bbd_coordinator_name') ?? ''); setMobile(localStorage.getItem('bbd_coordinator_mobile') ?? ''); me().then(user => { if (user.role !== 'FACULTY') { router.replace('/dashboard'); return; } Promise.all([schedulingApi.myFacultyTimetable(), apiClient.get<TimeSlot[] | { results: TimeSlot[] }>('/time-slots/')]).then(([response, slotResponse]) => { setData(response); setSlots(Array.isArray(slotResponse.data) ? slotResponse.data : slotResponse.data.results); }).catch(() => setError('Unable to load your timetable.')).finally(() => setLoading(false)); }).catch(() => router.replace('/login')); }, [router]);
  useEffect(() => { if (!selected || !data?.version) return; setSectionLoading(true); schedulingApi.sectionTimetable(data.version.id, selected).then(setEntries).catch(() => setError('Unable to load section timetable.')).finally(() => setSectionLoading(false)); }, [selected, data?.version?.id]);
  const section = data?.sections?.find(item => item.id === selected);
  const sectionYear = section?.year ?? Number(section?.name?.match(/(?:^|-)\s*([1-4])\s*[A-Z]/i)?.[1] ?? 1);
  const year = ['First Year', 'Second Year', 'Third Year', 'Fourth Year'][Math.max(0, sectionYear - 1)] ?? 'First Year';
  const semesterNumber = section?.semester_number ?? (data?.timetable?.semester?.toLowerCase().includes('even') ? sectionYear * 2 : sectionYear * 2 - 1);
  const semesterLabel = section?.semester_name ?? data?.timetable?.semester ?? 'Semester';
  const gridSlots: WorkloadGridSlot[] = periods.map((_, index) => ({ id: slots[index]?.id ?? `period-${index}`, is_break: slots[index]?.is_break ?? index === 4 }));
  const dayCells = (day: number) => {
    const startsBySlot = new Map<string, WorkloadGridEntry[]>();
    entries.filter(entry => entry.weekday === day).forEach(entry => {
      const starts = startsBySlot.get(entry.start_slot) ?? [];
      starts.push({ start_slot: entry.start_slot, block_length: entry.block_length });
      startsBySlot.set(entry.start_slot, starts);
    });
    const covered = new Set<number>();
    return periods.map((_, index) => {
      if (covered.has(index)) return null;
      const slot = slots[index];
      const startingEntries = slot ? entries.filter(item => item.weekday === day && item.start_slot === slot.id) : [];
      const entry = startingEntries.length === 1 ? startingEntries[0] : undefined;
      const span = entry ? getWorkloadCellSpan(gridSlots, index, getEntryPeriodCount(entry), startsBySlot) : 1;
      for (let offset = 1; offset < span; offset += 1) covered.add(index + offset);
      return <td key={`${day}-${index}`} colSpan={span} className={index === 4 ? 'lunch-cell' : ''}>
        {index === 4 ? ['L', 'U', 'N', 'C', 'H'][day] : startingEntries.length > 1
          ? startingEntries.map(item => <div key={item.id}>{notation(item)}</div>)
          : entry ? notation(entry) : slot?.is_break ? 'BREAK' : ''}
      </td>;
    });
  };
  const details = Array.from(new Map(entries.map(entry => [entry.course?.code ?? entry.course_offering, entry])).values());
  const save = () => { localStorage.setItem('bbd_coordinator_name', coordinator); localStorage.setItem('bbd_coordinator_mobile', mobile); setEditing(false); };
  if (loading) return <AdminLayout><Header title="My Timetable" /><main className="p-5 sm:p-8"><Card><CardContent className="p-8">Loading your timetable...</CardContent></Card></main></AdminLayout>;
  if (error && !data) return <AdminLayout><Header title="My Timetable" /><main className="p-5 sm:p-8"><p className="rounded bg-red-50 p-3 text-red-700">{error}</p></main></AdminLayout>;
  if (!data?.version) return <AdminLayout><Header title="My Timetable" /><main className="p-5 sm:p-8"><Card><CardContent className="p-8 text-center">No published timetable is available yet.</CardContent></Card></main></AdminLayout>;
  return <AdminLayout><Header title="My Timetable" /><main className="faculty-page p-5 sm:p-8"><div className="actions mb-4 flex items-center justify-between print:hidden"><div><h1 className="text-2xl font-semibold">My Timetable</h1><p className="text-sm text-slate-500">View your official published timetable.</p></div><div className="flex gap-2"><Button variant="outline" disabled={!selected || sectionLoading} onClick={() => window.print()}>Download PDF</Button><Button disabled={!selected || sectionLoading} onClick={() => window.print()}>Print</Button></div></div><Card className="mb-5 print:hidden"><CardContent className="flex flex-wrap items-center gap-3 p-5"><label className="font-medium">Select Section<select className="ml-2 min-w-64 rounded border p-2" value={selected} onChange={event => { setError(''); setSelected(event.target.value); }}><option value="">Select Section</option>{(data.sections ?? []).map(item => <option key={item.id} value={item.id}>{item.program_name ?? 'Program'} - {item.name ?? item.id}</option>)}</select></label><Button variant="outline" onClick={() => setEditing(true)}>Set Coordinator Details</Button></CardContent></Card>{editing && <Card className="mb-5 print:hidden"><CardContent className="flex flex-wrap items-end gap-3 p-5"><label>Class Coordinator<Input value={coordinator} onChange={event => setCoordinator(event.target.value)} /></label><label>Mobile No.<Input value={mobile} onChange={event => setMobile(event.target.value)} /></label><Button onClick={save}>Save</Button><Button variant="outline" onClick={() => setEditing(false)}>Cancel</Button></CardContent></Card>}{!selected ? <p className="rounded border bg-white p-8 text-center print:hidden">Select a section to view its timetable.</p> : sectionLoading ? <p className="rounded border bg-white p-8 text-center">Loading section timetable...</p> : error ? <p className="rounded bg-red-50 p-3 text-red-700">{error}</p> : <article className="official-sheet"><header className="official-heading"><h1>Babu Banarasi Das University</h1><h2>School of Engineering</h2><h3>Department of Computer Science &amp; Engineering</h3><p>B.Tech {year}, {semesterLabel}, Academic Session: {data.timetable?.academic_session ?? '—'}</p></header><table className="official-table"><thead><tr><th className="section-label" aria-label="Section" /><th className="time-label">Time/Day</th>{periods.map(period => <th key={period}>{period}</th>)}</tr></thead><tbody>{days.map(([label, day], dayIndex) => <tr key={label}>{dayIndex === 0 && <td className="section-label" rowSpan={days.length}>B.Tech CSE - {roman(semesterNumber)} Sem<br />Section: {section?.name ?? selected}</td>}<th className="time-label">{label}</th>{dayCells(day)}</tr>)}</tbody></table><table className="coordinator-table"><tbody><tr className="coordinator-row"><td colSpan={5}>Class Coordinator: {coordinator || '—'}</td><td colSpan={5}>Mobile No.: {mobile || '—'}</td></tr></tbody></table><table className="course-table"><thead><tr><th>Credit</th><th>Codes</th><th>Course Name</th><th>Meta Data</th><th>Faculty Name</th></tr></thead><tbody>{details.map(entry => <tr key={entry.id}><td>{entry.course?.credit ?? '—'}</td><td>{entry.course?.code ?? entry.course_offering}</td><td>{entry.course?.name ?? '—'}</td><td>{typeCode(entry.entry_type)}/{facultyCodes(entry)}</td><td>{facultyNames(entries, entry)}</td></tr>)}</tbody></table><footer className="signature-footer"><div>____________________________<br />Head - B.Tech (CSE)</div><div>____________________________<br />Coordinator - Academic Activities</div><div>____________________________<br />Dean (School of Engineering)</div></footer></article>}</main></AdminLayout>;
}
