'use client';
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { GenerationApplyError, GeneratedEntry, GenerationRun, TimeSlot, schedulingApi, listAll } from '@/lib/api/scheduling';
import { GenerationValidationPanel } from '@/components/GenerationValidationPanel';

type Person = { id: string; name?: string; initials?: string; employee_code?: string };
type Lookup = { id: string; code?: string; name?: string; course_name?: string; course_code?: string; course?: { id?: string; code?: string; name?: string } };
type Section = { id: string; name?: string; label?: string; program_name?: string; semester_name?: string };
const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
const human = (value?: string) => value?.replaceAll('_', ' ').toLowerCase().replace(/(^| )\w/g, char => char.toUpperCase()) ?? 'Unknown';
const personName = (person?: Person) => person?.name ?? ([person?.initials, person?.employee_code].filter(Boolean).join(' - ') || 'Faculty unavailable');
const courseLabel = (offering?: Lookup) => offering ? `${offering.course_code ?? offering.code ?? offering.course?.code ?? 'Unknown course'} - ${offering.course_name ?? offering.name ?? offering.course?.name ?? 'Unknown course'}` : 'Unknown course';

export function GenerationPreview({ run, slots, faculty, sections, onApply, applied, onAppliedVersion }: { run: GenerationRun; slots: TimeSlot[]; faculty: Person[]; sections: Section[]; onApply?: () => void; applied: string; onAppliedVersion?: () => void }) {
  void onApply;
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<GenerationApplyError>();
  const [appliedVersion, setAppliedVersion] = useState<string>();
  const [offerings, setOfferings] = useState<Lookup[]>([]);
  const [courses, setCourses] = useState<Lookup[]>([]);
  const safeFaculty = useMemo(() => Array.isArray(faculty) ? faculty : [], [faculty]);
  const safeSections = useMemo(() => Array.isArray(sections) ? sections : [], [sections]);
  const safeSlots = useMemo(() => Array.isArray(slots) ? slots : [], [slots]);
  const [loadedFaculty, setLoadedFaculty] = useState<Person[]>(safeFaculty);
  const [loadedSections, setLoadedSections] = useState<Section[]>(safeSections);
  const [loadedSlots, setLoadedSlots] = useState<TimeSlot[]>(safeSlots);
  const [rooms, setRooms] = useState<Lookup[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([listAll<Lookup>('/course-offerings/'), listAll<Lookup>('/courses/'), listAll<Person>('/faculty/'), listAll<Lookup>('/rooms/'), listAll<Section>('/sections/'), listAll<TimeSlot>('/time-slots/')])
      .then(([allOfferings, allCourses, allFaculty, allRooms, allSections, allSlots]) => {
        if (!live) return;
        setOfferings(allOfferings);
        setCourses(allCourses);
        setLoadedFaculty(safeFaculty.length ? safeFaculty : allFaculty);
        setRooms(allRooms);
        setLoadedSections(safeSections.length ? safeSections : allSections);
        setLoadedSlots(safeSlots.length ? safeSlots : allSlots);
      })
      .catch(() => undefined)
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [faculty, safeFaculty, safeSections, safeSlots, sections, slots]);

  const entries = run.result && typeof run.result === 'object' && Array.isArray(run.result.entries)
    ? run.result.entries
    : [];
  const entryCount = entries.length || run.statistics?.generated_entry_count || 0;
  const successful = ['OPTIMAL', 'FEASIBLE'].includes(run.solver_status ?? '') && entryCount > 0;
  const status = run.solver_status?.toUpperCase() ?? 'UNKNOWN';
  const wallTime = run.statistics?.wall_time ?? run.statistics?.solve_time_seconds;
  const isAlreadyApplied = Boolean(run.applied_at || run.applied_version || appliedVersion);
  const sectionIds = [...new Set(entries.map(entry => entry.section_id).filter((value): value is string => !!value))];
  const availableSections = sectionIds.length ? sectionIds : loadedSections.map(section => section.id);
  const [activeSection, setActiveSection] = useState(availableSections[0] ?? '');
  useEffect(() => { if (!activeSection && availableSections[0]) setActiveSection(availableSections[0]); }, [activeSection, availableSections]);

  const people = useMemo(() => new Map(loadedFaculty.map(person => [person.id, person])), [loadedFaculty]);
  const offeringMap = useMemo(() => {
    const result = new Map(offerings.map(offering => [offering.id, offering]));
    courses.forEach(course => result.set(course.id, course));
    return result;
  }, [offerings, courses]);
  const roomMap = useMemo(() => new Map(rooms.map(room => [room.id, room])), [rooms]);
  const visibleEntries = entries.filter(entry => !activeSection || entry.section_id === activeSection);
  const byCell = new Map(visibleEntries.map(entry => [`${entry.weekday}:${entry.start_slot_id}`, entry]));
  const covered = new Set<string>();
  const facultyLabel = (entry: GeneratedEntry) => (entry.faculty_assignments ?? entry.faculty)?.map(assignment => `${personName(people.get(assignment.faculty_id))} - ${human(assignment.role)}`).join(', ') || 'Faculty unavailable';
  const apply = async () => {
    if (applying || !successful || applied || isAlreadyApplied) return;
    setApplying(true);
    setApplyError(undefined);
    try {
      const result = await schedulingApi.applyGeneration(run.id);
      if (result.code === 'GENERATION_ALREADY_APPLIED') {
        setAppliedVersion(result.applied_version ?? undefined);
        setApplyError({ code: result.code, message: result.message, applied_version: result.applied_version });
        return;
      }
      setAppliedVersion(String(result.version_no));
      onAppliedVersion?.();
      setConfirming(false);
    } catch (error) {
      const response = (error as { response?: { data?: GenerationApplyError } }).response?.data;
      setApplyError(response ?? { code: 'APPLY_REQUEST_FAILED', message: 'The generated timetable could not be applied.' });
    } finally {
      setApplying(false);
    }
  };

  if (loading) return <Card><CardContent className="p-4 text-sm text-slate-600">Loading timetable details...</CardContent></Card>;
  if (!successful) {
    const message = status === 'UNKNOWN'
      ? 'Generation finished, but no feasible timetable was found within the solver time limit.'
      : status === 'INFEASIBLE'
        ? 'No feasible timetable could be created with the current constraints.'
        : entryCount === 0 && ['OPTIMAL', 'FEASIBLE'].includes(status)
          ? 'Generation completed without producing timetable entries.'
          : `Generation finished with solver status ${status}.`;
    return <Card><CardHeader><CardTitle>Generation result</CardTitle></CardHeader><CardContent className="space-y-3"><p className={`rounded p-3 ${status === 'UNKNOWN' ? 'bg-amber-50 text-amber-900' : 'bg-red-50 text-red-800'}`} role={status === 'UNKNOWN' ? 'status' : 'alert'}>{message}</p><dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Run ID</dt><dd className="break-all">{run.id}</dd></div><div><dt className="font-semibold">Solver status</dt><dd>{status}</dd></div><div><dt className="font-semibold">Generated entries</dt><dd>{entryCount}</dd></div><div><dt className="font-semibold">Solver wall time</dt><dd>{wallTime != null ? `${wallTime} seconds` : 'Not provided'}</dd></div></dl></CardContent></Card>;
  }

  return <Card><CardHeader><CardTitle>Generated timetable preview</CardTitle></CardHeader><CardContent className="space-y-4"><p className="rounded bg-green-50 p-3 text-green-800" role="status">Timetable generated successfully.</p><div className="grid gap-3 sm:grid-cols-4"><p><b>Run ID</b><br/><span className="break-all">{run.id}</span></p><p><b>Solver</b><br/>{status}</p><p><b>Generated entries</b><br/>{entryCount}</p><p><b>Solver wall time</b><br/>{wallTime != null ? `${wallTime} seconds` : '—'}</p></div>{availableSections.length > 1 && <label className="block text-sm font-medium">Preview section<select className="ml-2 rounded border p-2" value={activeSection} onChange={event => setActiveSection(event.target.value)}>{availableSections.map(id => { const section = loadedSections.find(item => item.id === id); return <option key={id} value={id}>{section ? [section.program_name, section.semester_name, section.name ?? section.label].filter(Boolean).join(' - ') : 'Section unavailable'}</option>; })}</select></label>}<div className="overflow-x-auto"><table className="min-w-[900px] w-full border-collapse text-sm"><thead><tr><th className="border p-2">Time slot</th>{days.map(day => <th className="border p-2" key={day}>{day}</th>)}</tr></thead><tbody>{loadedSlots.map((slot, index) => <tr key={slot.id}><th className={`border p-2 text-left ${slot.is_break ? 'bg-amber-50' : ''}`}>{slot.label ?? slot.start_time}{slot.is_break ? ' - BREAK' : ''}</th>{days.map((_, day) => { const key = `${day}:${slot.id}`; if (covered.has(key)) return null; const entry = byCell.get(key); if (!entry || slot.is_break) return <td className="h-20 min-w-36 border p-1 align-top" key={day}/>; let span = 1; for (let next = 1; next < entry.block_length && index + next < loadedSlots.length; next++) { if (loadedSlots[index + next].is_break) break; covered.add(`${day}:${loadedSlots[index + next].id}`); span++; } const room = entry.delivery_mode === 'ONLINE' ? 'Online' : roomMap.get(entry.room_id ?? '')?.code ?? roomMap.get(entry.room_id ?? '')?.name ?? 'Unknown room'; return <td className="min-w-36 border p-1 align-top" key={day} rowSpan={span}><div className={`rounded p-2 ${entry.state === 'FIXED' ? 'bg-slate-200' : entry.state === 'PRESERVED' ? 'bg-emerald-100' : 'bg-blue-100'}`}><b>{human(entry.state ?? 'GENERATED')}</b><div>{courseLabel(offeringMap.get(entry.course_offering_id ?? entry.course_offering ?? ''))}</div><div>{entry.entry_type ?? 'LECTURE'} - {entry.block_length} period(s)</div><div className="text-xs">{entry.delivery_mode === 'ONLINE' ? <span className="rounded bg-sky-100 px-1.5 py-0.5">Online</span> : `Room ${room}`}</div><div className="text-xs">{facultyLabel(entry)}</div></div></td>; })}</tr>)}</tbody></table></div>{appliedVersion || applied || run.applied_version ? <p className="rounded bg-green-50 p-3 text-green-700">Applied successfully{appliedVersion || applied ? ` to Version ${appliedVersion || applied}` : ''}</p> : <>{applyError && <GenerationValidationPanel error={applyError}/>} {confirming ? <div className="rounded border bg-slate-50 p-4"><p className="font-medium">Apply generated timetable?</p><p className="mt-1 text-sm text-slate-600">Candidate entries will be persisted in a new draft version after final backend validation.</p><div className="mt-3 flex gap-2"><Button variant="outline" onClick={() => setConfirming(false)} disabled={applying}>Cancel</Button><Button onClick={() => void apply()} disabled={applying}>{applying ? 'Applying...' : 'Apply'}</Button></div></div> : <Button disabled={applying || !successful} onClick={() => setConfirming(true)}>{applying ? 'Applying...' : 'Apply as New Draft Version'}</Button>}</>}</CardContent></Card>;
}
