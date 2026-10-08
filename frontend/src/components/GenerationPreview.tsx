'use client';
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { GenerationApplyError, GeneratedEntry, GenerationRun, ScheduleEntry, TimeSlot, schedulingApi, listAll, validateGenerationApply, recordGenerationRevalidation } from '@/lib/api/scheduling';
import { GenerationValidationPanel } from '@/components/GenerationValidationPanel';

type Person = { id: string; name?: string; initials?: string; employee_code?: string };
type Lookup = { id: string; code?: string; name?: string; course_name?: string; course_code?: string; course?: { id?: string; code?: string; name?: string }; faculty_assignments?: Person[] };
type Section = { id: string; name?: string; label?: string; program_name?: string; semester_name?: string };
const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
const human = (value?: string) => value?.replaceAll('_', ' ').toLowerCase().replace(/(^| )\w/g, char => char.toUpperCase()) ?? 'Unknown';
const personName = (person?: Person) => person?.name ?? ([person?.initials, person?.employee_code].filter(Boolean).join(' - ') || 'Faculty unavailable');
const courseLabel = (offering?: Lookup) => offering ? `${offering.course_code ?? offering.code ?? offering.course?.code ?? 'Unknown course'} - ${offering.course_name ?? offering.name ?? offering.course?.name ?? 'Unknown course'}` : 'Unknown course';

type GenerationPreviewProps = { run: GenerationRun; slots: TimeSlot[]; faculty: Person[]; sections: Section[]; onApply?: () => void; applied: string; onAppliedVersion?: () => void };
function GenerationPreviewContent({ run, slots, faculty, sections, onApply, applied, onAppliedVersion }: GenerationPreviewProps) {
  void onApply;
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<GenerationApplyError>();
  const [validatingApply, setValidatingApply] = useState(false);
  const [applyConfirmed, setApplyConfirmed] = useState(false);
  const [appliedVersion, setAppliedVersion] = useState<string>();
  const [sourceEntries, setSourceEntries] = useState<ScheduleEntry[]>([]);
  const [fixedEntries, setFixedEntries] = useState<GeneratedEntry[]>([]);
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
    Promise.all([listAll<Lookup>('/rooms/'), listAll<Section>('/sections/'), listAll<TimeSlot>('/time-slots/'), schedulingApi.entries(run.source_version)])
      .then(([allRooms, allSections, allSlots, allSourceEntries]) => {
        if (!live) return;
        setSourceEntries(allSourceEntries);
        setLoadedFaculty(safeFaculty);
        setRooms(allRooms);
        setLoadedSections(safeSections.length ? safeSections : allSections);
        setLoadedSlots(safeSlots.length ? safeSlots : allSlots);
        setFixedEntries(allSourceEntries.filter(entry => entry.locked).map(entry => ({
          id: entry.id,
          section_id: entry.section,
          course_offering_id: entry.course_offering,
          weekday: entry.weekday,
          start_slot_id: entry.start_slot,
          block_length: entry.block_length,
          room_id: entry.room ?? null,
          delivery_mode: entry.delivery_mode === 'ONLINE' ? 'ONLINE' : 'OFFLINE',
          faculty_assignments: entry.faculty_assignments?.map(({ faculty_id, role }) => ({ faculty_id, role })),
          entry_type: entry.entry_type,
          locked: true,
          state: 'FIXED',
        })));
      })
      .catch(() => undefined)
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [faculty, run.source_version, safeFaculty, safeSections, safeSlots, sections, slots]);

  useEffect(() => { if (!confirming) setApplyConfirmed(false); }, [confirming]);

  const entries = useMemo(() => run.result && typeof run.result === 'object' && Array.isArray(run.result.entries)
    ? run.result.entries
    : [], [run.result]);
  const entryCount = entries.length || run.statistics?.generated_entry_count || 0;
  const displayEntries = useMemo(() => [
    ...fixedEntries.filter(fixed => !entries.some(entry => entry.id === fixed.id)),
    ...entries,
  ], [entries, fixedEntries]);
  const status = run.solver_status?.toUpperCase() ?? 'UNKNOWN';
  const generationDiagnostics = run.diagnostics && !Array.isArray(run.diagnostics) ? run.diagnostics : undefined;
  const recordedErrors = generationDiagnostics?.blocking_errors?.length
    ? generationDiagnostics.blocking_errors
    : generationDiagnostics?.errors ?? [];
  const applyConflicts = applyError?.conflicts?.length ? applyError.conflicts : applyError?.errors ?? [];
  const validationConflicts = recordedErrors.length ? recordedErrors : applyConflicts;
  const blockingErrorCount = Math.max(run.statistics?.blocking_error_count ?? 0, recordedErrors.length, applyError?.blocking_error_count ?? 0, applyError?.error_count ?? 0, applyConflicts.length);
  const finalValidationFailed = run.statistics?.final_validation_passed === false || status === 'POST_VALIDATION_FAILED' || blockingErrorCount > 0;
  const validationNotRecorded = run.statistics?.final_validation_passed !== true && !finalValidationFailed;
  const applyBlocked = finalValidationFailed || validationNotRecorded || (applyError?.error_count ?? applyError?.conflicts?.length ?? 0) > 0;
  const successful = ['OPTIMAL', 'FEASIBLE'].includes(status) && entryCount > 0 && !applyBlocked;
  const wallTime = run.statistics?.wall_time ?? run.statistics?.solve_time_seconds;
  const isAlreadyApplied = Boolean(run.applied_at || run.applied_version || appliedVersion);
  const sectionIds = [...new Set(displayEntries.map(entry => entry.section_id).filter((value): value is string => !!value))];
  const availableSections = sectionIds.length ? sectionIds : loadedSections.map(section => section.id);
  const [activeSection, setActiveSection] = useState(availableSections[0] ?? '');
  useEffect(() => { if (!activeSection && availableSections[0]) setActiveSection(availableSections[0]); }, [activeSection, availableSections]);

  const offeringMap = useMemo(() => {
    return new Map(sourceEntries.map(entry => [entry.course_offering, {
      id: entry.course_offering,
      course: entry.course,
      course_code: entry.course?.code,
      course_name: entry.course?.name,
    }]));
  }, [sourceEntries]);
  const people = useMemo(() => new Map([
    ...loadedFaculty.map(person => [person.id, person] as const),
    ...sourceEntries.flatMap(entry => (entry.faculty_assignments ?? []).map(person => [person.faculty_id, {
      id: person.faculty_id,
      name: person.name,
      initials: person.initials,
    }] as const)),
  ]), [loadedFaculty, sourceEntries]);
  const roomMap = useMemo(() => new Map(rooms.map(room => [room.id, room])), [rooms]);
  const visibleEntries = displayEntries.filter(entry => !activeSection || entry.section_id === activeSection);
  const byCell = new Map(visibleEntries.map(entry => [`${entry.weekday}:${entry.start_slot_id}`, entry]));
  const covered = new Set<string>();
  const facultyLabel = (entry: GeneratedEntry) => (entry.faculty_assignments ?? entry.faculty)?.map(assignment => `${personName(people.get(assignment.faculty_id))} - ${human(assignment.role)}`).join(', ') || 'Faculty unavailable';
  const apply = async () => {
    if (applying || validatingApply || !successful || applied || isAlreadyApplied) return;
    if (!applyConfirmed) {
      setValidatingApply(true); setApplyError(undefined);
      try {
        const validation = await validateGenerationApply(run.id);
        if (!validation.valid) {
          setApplyError({ ...validation, code: 'GENERATED_TIMETABLE_VALIDATION_FAILED', message: 'Current Apply validation found blocking errors.' });
          return;
        }
        setApplyConfirmed(true);
        return;
      } catch (error) {
        const response = (error as { response?: { data?: GenerationApplyError } }).response?.data;
        setApplyError(response ?? { code: 'APPLY_VALIDATION_REQUEST_FAILED', message: 'Could not validate the candidate for Apply.' });
        return;
      } finally {
        setValidatingApply(false);
      }
    }
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
      setConfirming(false);
    } finally {
      setApplying(false);
    }
  };

  if (loading) return <Card><CardContent className="p-4 text-sm text-slate-600">Loading timetable details...</CardContent></Card>;
  if (!successful) {
    const message = finalValidationFailed
      ? `Cannot apply: resolve ${blockingErrorCount} blocking errors first.`
      : validationNotRecorded
      ? 'Final hard-rule validation is not recorded for this run. It cannot be applied.'
      : status === 'UNKNOWN'
      ? 'Generation finished, but no feasible timetable was found within the solver time limit.'
      : status === 'INFEASIBLE'
        ? 'No feasible timetable could be created with the current constraints.'
        : entryCount === 0 && ['OPTIMAL', 'FEASIBLE'].includes(status)
          ? 'Generation completed without producing timetable entries.'
          : `Generation finished with solver status ${status}.`;
    return <Card><CardHeader><CardTitle>Generation result</CardTitle></CardHeader><CardContent className="space-y-3"><p className={`rounded p-3 ${status === 'UNKNOWN' ? 'bg-amber-50 text-amber-900' : 'bg-red-50 text-red-800'}`} role={status === 'UNKNOWN' ? 'status' : 'alert'}>{message}</p>{finalValidationFailed && <GenerationValidationPanel error={{code:'POST_VALIDATION_FAILED',message:'The candidate failed final hard-rule validation and cannot be applied.',conflicts:validationConflicts,error_count:blockingErrorCount,blocking_error_count:blockingErrorCount,warning_count:0}}/>}<dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Run ID</dt><dd className="break-all">{run.id}</dd></div><div><dt className="font-semibold">Solver status</dt><dd>{status}</dd></div><div><dt className="font-semibold">Generated entries</dt><dd>{entryCount}</dd></div><div><dt className="font-semibold">Solver wall time</dt><dd>{wallTime != null ? `${wallTime} seconds` : 'Not provided'}</dd></div></dl></CardContent></Card>;
  }

  return <Card><CardHeader><CardTitle>Generated timetable preview</CardTitle></CardHeader><CardContent className="space-y-4"><p className="rounded bg-green-50 p-3 text-green-800" role="status">Timetable generated successfully.</p><div className="grid gap-3 sm:grid-cols-4"><p><b>Run ID</b><br/><span className="break-all">{run.id}</span></p><p><b>Solver</b><br/>{status}</p><p><b>Generated entries</b><br/>{entryCount}</p><p><b>Solver wall time</b><br/>{wallTime != null ? `${wallTime} seconds` : '—'}</p></div>{availableSections.length > 1 && <label className="block text-sm font-medium">Preview section<select className="ml-2 rounded border p-2" value={activeSection} onChange={event => setActiveSection(event.target.value)}>{availableSections.map(id => { const section = loadedSections.find(item => item.id === id); return <option key={id} value={id}>{section ? [section.program_name, section.semester_name, section.name ?? section.label].filter(Boolean).join(' - ') : 'Section unavailable'}</option>; })}</select></label>}<div className="overflow-x-auto"><table className="min-w-[900px] w-full border-collapse text-sm"><thead><tr><th className="border p-2">Time slot</th>{days.map(day => <th className="border p-2" key={day}>{day}</th>)}</tr></thead><tbody>{loadedSlots.map((slot, index) => <tr key={slot.id}><th className={`border p-2 text-left ${slot.is_break ? 'bg-amber-50' : ''}`}>{slot.label ?? slot.start_time}{slot.is_break ? ' - BREAK' : ''}</th>{days.map((_, day) => { const key = `${day}:${slot.id}`; if (covered.has(key)) return null; const entry = byCell.get(key); if (!entry || slot.is_break) return <td className="h-20 min-w-36 border p-1 align-top" key={day}/>; let span = 1; for (let next = 1; next < entry.block_length && index + next < loadedSlots.length; next++) { if (loadedSlots[index + next].is_break) break; covered.add(`${day}:${loadedSlots[index + next].id}`); span++; } const room = roomMap.get(entry.room_id ?? '')?.code ?? roomMap.get(entry.room_id ?? '')?.name ?? 'pending'; return <td className="min-w-36 border p-1 align-top" key={day} rowSpan={span}><div className={`rounded p-2 ${entry.state === 'FIXED' ? 'bg-slate-200' : entry.state === 'PRESERVED' ? 'bg-emerald-100' : 'bg-blue-100'}`}><b>{human(entry.state ?? 'GENERATED')}</b><div>{courseLabel(offeringMap.get(entry.course_offering_id ?? entry.course_offering ?? ''))}</div><div>{entry.entry_type ?? 'LECTURE'} - {entry.block_length} period(s)</div><div className="text-xs">{entry.delivery_mode === 'ONLINE' ? <span className="rounded bg-sky-100 px-1.5 py-0.5">ONLINE</span> : `OFFLINE - Room ${room}`}</div><div className="text-xs">{facultyLabel(entry)}</div></div></td>; })}</tr>)}</tbody></table></div>{appliedVersion || applied || run.applied_version ? <p className="rounded bg-green-50 p-3 text-green-700">Applied successfully{appliedVersion || applied ? ` to Version ${appliedVersion || applied}` : ''}</p> : <>{applyError && <GenerationValidationPanel error={applyError}/>} {confirming ? <div className="rounded border bg-slate-50 p-4"><p className="font-medium">Apply generated timetable?</p><p className="mt-1 text-sm text-slate-600">Candidate entries will be persisted in a new draft version after final backend validation.</p><div className="mt-3 flex gap-2"><Button variant="outline" onClick={() => setConfirming(false)} disabled={applying}>Cancel</Button><Button onClick={() => void apply()} disabled={applying}>{applying ? 'Applying...' : 'Apply'}</Button></div></div> : <Button disabled={applying || !successful} onClick={() => setConfirming(true)}>{applying ? 'Applying...' : 'Apply as New Draft Version'}</Button>}</>}</CardContent></Card>;
}

function GenerationProvenanceCard({ run }: { run: GenerationRun }) {
  const [current, setCurrent] = useState<(GenerationApplyError & {valid:boolean})>();
  const [recording, setRecording] = useState(false);
  const [recorded, setRecorded] = useState(false);
  useEffect(() => {
    let live = true;
    validateGenerationApply(run.id).then(value => { if (live) setCurrent(value); }).catch(() => undefined);
    return () => { live = false; };
  }, [run.id]);
  const generationValid = run.generation_validation?.valid ?? run.statistics?.final_validation_passed;
  const generationCount = run.generation_validation?.blocker_count ?? run.generation_validation?.errors?.length ?? run.statistics?.blocking_error_count ?? 0;
  const applyState = run.apply_validation;
  const record = async () => { setRecording(true); try { const value = await recordGenerationRevalidation(run.id); setCurrent(value); setRecorded(true); } finally { setRecording(false); } };
  return <Card><CardHeader><CardTitle>Validation &amp; provenance</CardTitle></CardHeader><CardContent className="space-y-3"><dl className="grid gap-2 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Generation validation</dt><dd>{generationValid === undefined ? 'Not recorded' : `${generationValid ? 'Passed' : 'Failed'} (${generationCount} blockers)`}</dd></div><div><dt className="font-semibold">Current validation</dt><dd>{current ? `${current.valid ? 'Passed' : 'Failed'} (${current.blocking_error_count ?? current.errors?.length ?? 0} blockers)` : 'Checking...'}</dd></div><div><dt className="font-semibold">Source fingerprint</dt><dd>{current ? current.source_fingerprint_matches ? 'Match' : 'Changed' : 'Not checked'}</dd></div><div><dt className="font-semibold">Input fingerprint</dt><dd>{run.provenance_status === 'LEGACY_NO_SNAPSHOT' ? 'Legacy run' : run.input_fingerprint_matches === null || run.input_fingerprint_matches === undefined ? 'Not recorded' : run.input_fingerprint_matches ? 'Match' : 'Changed'}</dd></div><div><dt className="font-semibold">Apply validation</dt><dd>{applyState ? `${applyState.valid ? 'Passed' : 'Failed'} (${applyState.blocker_count ?? 0} blockers)` : 'Not run'}</dd></div></dl><Button variant="outline" onClick={() => void record()} disabled={recording}>{recording ? 'Recording...' : recorded ? 'Current validation recorded' : 'Record current revalidation'}</Button>{current && !current.valid && <GenerationValidationPanel error={{...current,code:'GENERATED_TIMETABLE_VALIDATION_FAILED',message:'Current Apply validation found blocking errors.'}}/>}</CardContent></Card>;
}

export function GenerationPreview(props: GenerationPreviewProps) {
  return <><GenerationProvenanceCard run={props.run}/><GenerationPreviewContent {...props}/></>;
}
