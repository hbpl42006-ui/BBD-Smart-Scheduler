'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { me } from '@/lib/api/auth';
import { canGenerateTimetable, Role } from '@/lib/permissions';
import {
  GenerationMode,
  GenerationDiagnostics,
  GenerationPreflightResult,
  GenerationRun,
  GenerationSettings,
  OfferingGenerationRule,
  schedulingApi,
  Version,
  listAll,
  createInitialDraftVersion,
} from '@/lib/api/scheduling';
import { PreflightDiagnostics } from '@/components/PreflightDiagnostics';
import { GenerationPreview } from '@/components/GenerationPreview';
import { GenerationValidationPanel } from '@/components/GenerationValidationPanel';

type Option = {
  id: string;
  name?: string;
  program_name?: string;
  section?: string;
  section_id?: string;
  course_name?: string;
  course_code?: string;
  weekly_periods?: number;
  default_class_type?: string;
  required_block_size?: number;
  allow_remainder_period?: boolean;
  room_type_requirement?: string;
  delivery_policy?: 'STANDARD' | 'HYBRID';
  offline_weekday?: number | null;
  effective_delivery_policy?: 'STANDARD' | 'HYBRID';
  effective_offline_weekday?: number | null;
  faculty_assignments?: { faculty_id: string; role?: string }[];
};
type Preferences = {
  spread_course_days: number;
  balance_section_load: number;
  minimize_section_gaps: number;
  minimize_faculty_gaps: number;
  preserve_existing: number;
  random_seed: number;
  max_solve_seconds: number;
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const weekdayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
const LABEL_SEPARATOR = ' \u00b7 ';
const initialPreferences: Preferences = {
  spread_course_days: 50,
  balance_section_load: 40,
  minimize_section_gaps: 60,
  minimize_faculty_gaps: 40,
  preserve_existing: 70,
  random_seed: 42,
  max_solve_seconds: 120,
};
const sectionId = (item: Option) => item.section_id ?? item.section ?? '';
const generationDiagnostics = (run: GenerationRun) => {
  const value = run.diagnostics;
  if (Array.isArray(value)) return { errors: value, warnings: [] as GenerationDiagnostics[] };
  const errors = value?.errors ?? [];
  const warnings = [...(value?.warnings ?? []), ...errors.filter(item => item.code === 'ROOM_ELIGIBILITY_EXCEPTION_APPLIED')];
  return { errors: errors.filter(item => item.code !== 'ROOM_ELIGIBILITY_EXCEPTION_APPLIED'), warnings };
};

const fromOffering = (item: Option): OfferingGenerationRule => {
  const entryType = item.default_class_type ?? 'LECTURE';
  const block = entryType === 'LECTURE' ? 1 : Math.max(1, item.required_block_size ?? 1);
  const periods = item.weekly_periods ?? 0;
  return {
    course_offering_id: item.id,
    faculty: (item.faculty_assignments ?? []).map(assignment => ({
      faculty_id: assignment.faculty_id,
      role: assignment.role ?? 'PRIMARY',
    })),
    session_lengths: Array.from({ length: Math.floor(periods / block) }, () => block).concat(item.allow_remainder_period && periods % block ? [periods % block] : []),
    entry_type: entryType,
    room_type: item.room_type_requirement || undefined,
    block_size: block,
    allow_remainder_period: item.allow_remainder_period ?? false,
  };
};

export default function Page() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [role, setRole] = useState<Role>();
  const [versions, setVersions] = useState<Version[]>([]);
  const [source, setSource] = useState('');
  const [sections, setSections] = useState<Option[]>([]);
  const [all, setAll] = useState<Option[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [rules, setRules] = useState<OfferingGenerationRule[]>([]);
  const [preferences, setPreferences] = useState(initialPreferences);
  const [mode, setMode] = useState<GenerationMode>('FILL_GAPS');
  const [step, setStep] = useState(1);
  const [preflightResult, setPreflightResult] = useState<GenerationPreflightResult>();
  const [run, setRun] = useState<GenerationRun>();
  const [error, setError] = useState('');
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [generationBusy, setGenerationBusy] = useState(false);
  const [generationElapsedSeconds, setGenerationElapsedSeconds] = useState(0);
  const [configured, setConfigured] = useState(false);
  const [versionsLoaded, setVersionsLoaded] = useState(false);
  const [timetableMissing, setTimetableMissing] = useState(false);
  const [showGeneratedPreview, setShowGeneratedPreview] = useState(false);

  useEffect(() => {
    if (!generationBusy) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setGenerationElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [generationBusy]);

  const offerings = useMemo(() => {
    const sectionIds = new Set(selected);
    return all.filter(item => sectionIds.has(sectionId(item)));
  }, [all, selected]);
  const configure = () => setRules(previous => offerings.map(item =>
    previous.find(rule => rule.course_offering_id === item.id) ?? fromOffering(item),
  ));
  const payload = (): GenerationSettings => ({
    mode,
    section_ids: selected,
    offering_rules: offerings.map(item =>
      rules.find(rule => rule.course_offering_id === item.id) ?? fromOffering(item),
    ),
    soft_constraints: {
      spread_course_days: preferences.spread_course_days,
      balance_section_load: preferences.balance_section_load,
      minimize_section_gaps: preferences.minimize_section_gaps,
      minimize_faculty_gaps: preferences.minimize_faculty_gaps,
      preserve_existing: preferences.preserve_existing,
    },
    max_solve_seconds: preferences.max_solve_seconds,
    random_seed: preferences.random_seed,
  });

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      me(),
      schedulingApi.list(),
      schedulingApi.listVersions(id),
      listAll<Option>('/sections/'),
      listAll<Option>('/course-offerings/'),
    ]).then(([user, timetables, versionList, sectionList, offeringList]) => {
      if (cancelled) return;
      const timetable = timetables.find(item => item.id === id);
      if (!timetable) {
        setTimetableMissing(true);
        setError('This timetable no longer exists. Please select or create a timetable.');
        router.replace('/timetables?notice=timetable-missing');
        return;
      }
      setRole(user.role as Role);
      setVersions(versionList);
      setSource(versionList[0]?.id && UUID_RE.test(versionList[0].id) ? versionList[0].id : '');
      setSections(sectionList);
      setAll(offeringList);
      setVersionsLoaded(true);
      console.log('[GENERATE] timetableId =', timetable.id);
      console.log('[GENERATE] versions =', versionList);
      console.log('[GENERATE] selectedVersionId =', versionList[0]?.id ?? '');
    }).catch((cause: unknown) => {
      if (cancelled) return;
      const status = (cause as { response?: { status?: number } }).response?.status;
      if (status === 404) {
        setTimetableMissing(true);
        setError('This timetable no longer exists. Please select or create a timetable.');
        router.replace('/timetables?notice=timetable-missing');
        return;
      }
      setVersionsLoaded(true);
      setError('Unable to load generator data.');
    });
    return () => { cancelled = true; };
  }, [id, router]);

  useEffect(() => {
    if (!UUID_RE.test(id)) return;
    let cancelled = false;
    void schedulingApi.detail(id).catch((cause: unknown) => {
      if (cancelled) return;
      if ((cause as { response?: { status?: number } }).response?.status === 404) {
        setTimetableMissing(true);
        setError('This timetable no longer exists. Please select or create a timetable.');
        router.replace('/timetables?notice=timetable-missing');
      }
    });
    return () => { cancelled = true; };
  }, [id, router]);

  const createDraftVersion = async () => {
    if (!id.trim() || !UUID_RE.test(id)) {
      setError('This timetable no longer exists. Please select or create a timetable.');
      return;
    }
    setGenerationBusy(true);
    setError('');
    try {
      const version = await createInitialDraftVersion(id);
      setVersions([version]);
      setSource(version.id);
      setPreflightResult(undefined);
    } catch (cause: unknown) {
      const status = (cause as { response?: { status?: number } }).response?.status;
      if (status === 404) {
        setError('This timetable no longer exists. Please select or create a timetable.');
        router.replace('/timetables?notice=timetable-missing');
      } else setError('Could not create a draft version. Please try again.');
    } finally {
      setGenerationBusy(false);
    }
  };

  const runPreflight = async () => {
    if (!source.trim()) {
      setError('No timetable version is selected. Create a draft version before running preflight.');
      return;
    }
    if (!UUID_RE.test(source)) {
      setError('The selected timetable version ID is invalid. Reload the timetable versions and try again.');
      return;
    }
    setPreflightBusy(true);
    setError('');
    try {
      setPreflightResult(await schedulingApi.generationPreflight(source, payload()));
    } catch (cause: unknown) {
      const status = (cause as { response?: { status?: number } }).response?.status;
      setError(status === 404
        ? 'This timetable version no longer exists. Select a current version from the timetable.'
        : 'Preflight could not be completed. Review the response or try again.');
    } finally {
      setPreflightBusy(false);
    }
  };

  const generate = async () => {
    if (!source.trim()) {
      setError('No timetable version is selected. Create a draft version before generating.');
      return;
    }
    if (!UUID_RE.test(source)) {
      setError('The selected timetable version ID is invalid. Reload the timetable versions and try again.');
      return;
    }
    if (!preflightResult?.valid || generationBusy) return;
    setGenerationElapsedSeconds(0);
    setGenerationBusy(true);
    setError('');
    try {
      setRun(await schedulingApi.createGeneration(source, payload()));
      setStep(5);
    } catch (cause: unknown) {
      const status = (cause as { response?: { status?: number } }).response?.status;
      setError(status === 404
        ? 'This timetable version no longer exists. Select a current version from the timetable.'
        : 'Generation could not be started. Please try again.');
    } finally {
      setGenerationBusy(false);
    }
  };

  if (canGenerateTimetable(role) && !versionsLoaded && !timetableMissing) {
    return <AdminLayout><Header title="Generate Timetable" /><main className="mx-auto max-w-7xl p-6"><Card><CardContent className="p-6">Loading timetable versions...</CardContent></Card></main></AdminLayout>;
  }
  if (!canGenerateTimetable(role)) {
    return <AdminLayout><Header title="Generate Timetable" /><main className="p-6"><Card><CardContent className="p-8">Your role cannot generate timetables.</CardContent></Card></main></AdminLayout>;
  }
  if (versionsLoaded && !versions.some(version => UUID_RE.test(version.id))) {
    return <AdminLayout><Header title="Generate Timetable" /><main className="mx-auto max-w-7xl space-y-5 p-6">{timetableMissing && <p className="rounded bg-red-50 p-3 text-red-700">This timetable no longer exists. Please select or create a timetable.</p>}<Card><CardHeader><CardTitle>Scope</CardTitle></CardHeader><CardContent><p className="font-medium">No timetable version exists for this timetable.</p><p className="mt-1 text-sm text-slate-600">No timetable version exists. Create a draft version before running preflight.</p>{error && <p className="mt-3 text-sm text-red-700">{error}</p>}<div className="mt-4 flex gap-2"><Button type="button" disabled={generationBusy || timetableMissing} onClick={() => void createDraftVersion()}>{generationBusy ? 'Creating draft...' : 'Create Draft Version'}</Button><Link href="/timetables"><Button type="button" variant="outline">Back to Timetables</Button></Link></div></CardContent></Card></main></AdminLayout>;
  }

  if (step === 5 && run) {
    const solverStatus = run.solver_status?.toUpperCase() ?? 'UNKNOWN';
    const generatedEntryCount = run.result?.entries?.length ?? run.statistics?.generated_entry_count ?? 0;
    const diagnostics = generationDiagnostics(run);
    const validationErrors = run.diagnostics && !Array.isArray(run.diagnostics) && run.diagnostics.blocking_errors?.length
      ? run.diagnostics.blocking_errors
      : diagnostics.errors;
    const blockingErrorCount = Math.max(run.statistics?.blocking_error_count ?? 0, validationErrors.length);
    const finalValidationPassed = run.statistics?.final_validation_passed === true && blockingErrorCount === 0 && solverStatus !== 'POST_VALIDATION_FAILED';
    const solverFoundCandidate = ['OPTIMAL', 'FEASIBLE'].includes(solverStatus) && generatedEntryCount > 0;
    const successful = solverFoundCandidate && finalValidationPassed;
    const wallTime = run.statistics?.wall_time ?? run.statistics?.solve_time_seconds;
    const requestedSolveTime = run.input_config?.max_solve_seconds ?? 120;
    const coreItems = diagnostics.errors.flatMap(item => item.core ?? []);
    const blockerItems = diagnostics.errors.flatMap(item => item.core?.length ? item.core : [item]);
    const diagnosticLabel = (item: GenerationDiagnostics) => {
      const offering = all.find(value => value.id === item.course_offering_id);
      const section = sections.find(value => value.id === item.section_id);
      const course = item.course_code ?? offering?.course_code ?? 'Scheduling constraint';
      const sectionName = item.section ?? section?.name ?? section?.section ?? '';
      return [course, sectionName].filter(Boolean).join(' / ');
    };
    const message = successful
      ? 'Timetable generated successfully.'
      : solverFoundCandidate && !finalValidationPassed
        ? run.statistics?.final_validation_passed === false || blockingErrorCount > 0
          ? `Solver found a candidate, but final validation failed with ${blockingErrorCount} blocking error${blockingErrorCount === 1 ? '' : 's'}.`
          : 'Solver found a candidate. Final validation is not recorded, so this candidate cannot be applied.'
      : solverStatus === 'PRECHECK_FAILED'
        ? 'Generation stopped before CP-SAT because one or more requirements have no valid placement.'
      : solverStatus === 'UNKNOWN'
        ? 'Generation finished, but no feasible timetable was found within the solver time limit.'
        : solverStatus === 'INFEASIBLE'
          ? 'No feasible timetable could be created with the current constraints.'
          : generatedEntryCount === 0
            ? 'Generation completed without producing timetable entries.'
            : `Generation finished with solver status ${solverStatus}.`;
    const resultStyle = successful
      ? 'bg-green-50 text-green-800'
      : solverStatus === 'UNKNOWN'
        ? 'bg-amber-50 text-amber-900'
        : 'bg-red-50 text-red-800';
    return <AdminLayout><Header title="Generate Timetable" /><main className="mx-auto max-w-7xl space-y-5 p-6"><Card><CardHeader><CardTitle>Generation result</CardTitle></CardHeader><CardContent className="space-y-4"><p className={`rounded p-3 ${resultStyle}`} role={successful ? 'status' : 'alert'}>{message}</p><dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Run ID</dt><dd className="break-all">{run.id}</dd></div><div><dt className="font-semibold">Solver status</dt><dd>{solverStatus}</dd></div><div><dt className="font-semibold">Generated entries</dt><dd>{generatedEntryCount}</dd></div><div><dt className="font-semibold">Requested solve time</dt><dd>{requestedSolveTime} seconds</dd></div><div><dt className="font-semibold">Actual solver wall time</dt><dd>{wallTime != null ? `${wallTime} seconds` : run.statistics?.solver_invoked === false ? 'Not run (pre-solver check)' : 'Not recorded'}</dd></div><div><dt className="font-semibold">Phase 1 status / wall time</dt><dd>{run.statistics?.phase1_status ?? 'Not recorded'} / {run.statistics?.phase1_wall_time ?? '—'} seconds</dd></div><div><dt className="font-semibold">Phase 2 status / wall time</dt><dd>{run.statistics?.phase2_status ?? 'Not run'} / {run.statistics?.phase2_wall_time ?? 0} seconds</dd></div></dl>{!successful && <details className="rounded border bg-slate-50 p-3"><summary className="cursor-pointer font-medium">View solver diagnostics</summary><div className="mt-3 space-y-3 text-sm">{run.statistics?.solver_invoked === false ? <p>CP-SAT was not invoked; there is no solver wall time or infeasible core for this run.</p> : <dl className="grid gap-2 sm:grid-cols-3"><div><dt className="font-medium">Model variables</dt><dd>{run.statistics?.variable_count ?? 'Not recorded'}</dd></div><div><dt className="font-medium">Constraints</dt><dd>{run.statistics?.constraint_count ?? 'Not recorded'}</dd></div><div><dt className="font-medium">Conflicts / branches / propagations</dt><dd>{run.statistics?.num_conflicts ?? '?'} / {run.statistics?.num_branches ?? '?'} / {run.statistics?.num_propagations ?? '?'}</dd></div></dl>}{blockerItems.length ? <section><h3 className="font-semibold">Likely blockers</h3><ul className="mt-1 list-disc space-y-1 pl-5">{blockerItems.map((item, index) => <li key={`${item.code}-${index}`}><span className="font-medium">{diagnosticLabel(item)}</span>{item.activity_type ? ` - ${item.activity_type}, ${item.block_length ?? 1} period(s)` : ''}: {item.constraint ?? item.message ?? item.code ?? 'Scheduling constraint'}</li>)}</ul></section> : <p>No blocking diagnostics were recorded.</p>}{coreItems.length > 0 && <p className="text-slate-600">The core is a sufficient explanation, not necessarily the smallest possible conflict set.</p>}{diagnostics.warnings.length > 0 && <section><h3 className="font-semibold">Policy notices</h3><ul className="mt-1 list-disc space-y-1 pl-5">{diagnostics.warnings.map((item, index) => <li key={`${item.code}-${index}`}>{item.message ?? item.code}</li>)}</ul></section>}</div></details>}<div className="flex gap-2"><Button type="button" variant="outline" onClick={() => setStep(4)}>Back to Preflight</Button>{successful && <Button type="button" onClick={() => setShowGeneratedPreview(value => !value)}>{showGeneratedPreview ? 'Hide Generated Timetable' : 'Preview Generated Timetable'}</Button>}</div></CardContent></Card>{!successful && solverFoundCandidate && <GenerationValidationPanel error={{code:'POST_VALIDATION_FAILED',message:'The candidate failed or lacks recorded final hard-rule validation and cannot be applied.',conflicts:validationErrors,error_count:blockingErrorCount,blocking_error_count:blockingErrorCount,warning_count:0}}/>}{showGeneratedPreview && successful && <GenerationPreview run={run} slots={[]} faculty={[]} sections={[]} applied="" onAppliedVersion={() => setShowGeneratedPreview(false)} />}</main></AdminLayout>;
  }

  return <AdminLayout><Header title="Generate Timetable" /><main className="mx-auto max-w-7xl space-y-5 p-6">{error && <p className="rounded bg-red-50 p-3 text-red-700">{error}</p>}{step <= 3 && <Card><CardHeader><CardTitle>{step === 1 ? 'Scope' : step === 2 ? 'Offerings' : 'Preferences'}</CardTitle></CardHeader><CardContent>
    {step === 1 ? <>
      <select className="rounded border p-2" value={source} onChange={event => setSource(event.target.value)}>{versions.map(version => <option key={version.id} value={version.id}>{[`v${version.version_no}`, version.status].filter(Boolean).join(LABEL_SEPARATOR)}</option>)}</select>
      <div className="my-4">{(['FILL_GAPS', 'REBUILD_UNLOCKED'] as GenerationMode[]).map(value => <label className="mr-4" key={value}><input type="radio" checked={mode === value} onChange={() => setMode(value)} /> {value}</label>)}</div>
      <p className="mb-4 text-sm text-slate-600">{mode === 'FILL_GAPS' ? 'Keep all existing entries and fill missing periods.' : 'Keep locked entries and entries outside selected sections; rebuild unlocked entries.'}</p>
      {sections.map(section => {
        const effectivePolicy = section.effective_delivery_policy ?? section.delivery_policy;
        const offlineWeekday = section.effective_offline_weekday ?? section.offline_weekday;
        const hybrid = effectivePolicy === 'HYBRID';
        const sectionLabel = [section.program_name ?? 'Section', section.name ?? section.id].filter(Boolean).join(LABEL_SEPARATOR);
        return <label className="mr-2 mb-2 inline-flex items-center gap-2 rounded border p-2" key={section.id}>
          <input type="checkbox" checked={selected.includes(section.id)} onChange={event => setSelected(event.target.checked ? [...selected, section.id] : selected.filter(value => value !== section.id))} />
          <span>{sectionLabel}{hybrid && <span className="ml-2 text-xs text-amber-800">Hybrid - Offline {offlineWeekday == null ? 'day not configured' : weekdayNames[offlineWeekday] ?? `invalid non-working day (${offlineWeekday})`}; online on other days</span>}</span>
        </label>;
      })}
    </> : step === 2 ? <>
      <div className="mb-4 flex gap-2"><Button type="button" onClick={() => { configure(); setConfigured(true); }}>Auto Configure All</Button><span className="text-sm">Offerings: {offerings.length}</span></div>
      {configured && <p className="mb-3 rounded bg-green-50 p-3 text-sm text-green-700">Configuration loaded from Course Offerings</p>}
      {offerings.map(item => {
        const rule = rules.find(value => value.course_offering_id === item.id) ?? fromOffering(item);
        const periods = item.weekly_periods ?? 0;
        const blockSize = rule.block_size ?? 2;
        return <div className="mb-3 rounded border p-4" key={item.id}>
          <b>{item.course_name ?? item.course_code ?? item.id}</b><p className="text-sm">Faculty assignments: {rule.faculty.length}</p>
          {rule.entry_type === 'PRACTICAL' && <label className="mt-2 block text-sm"><input type="checkbox" checked={rule.allow_remainder_period === true} onChange={event => setRules(previous => previous.map(value => value.course_offering_id === item.id ? {
            ...value,
            allow_remainder_period: event.target.checked,
            session_lengths: event.target.checked && periods % blockSize ? [...Array(Math.floor(periods / blockSize)).fill(blockSize), periods % blockSize] : value.session_lengths,
          } : value))} /> Allow single remainder period</label>}
        </div>;
      })}
    </> : <div className="grid gap-4 sm:grid-cols-2"><label>Max solve time<select className="mt-1 block w-full rounded border p-2" value={preferences.max_solve_seconds} onChange={event => setPreferences(value => ({ ...value, max_solve_seconds: Number(event.target.value) }))}>{[30, 60, 120, 300].map(seconds => <option key={seconds} value={seconds}>{seconds} seconds</option>)}</select></label><label>Random seed<input className="mt-1 block w-full rounded border p-2" type="number" value={preferences.random_seed} onChange={event => setPreferences(value => ({ ...value, random_seed: Number(event.target.value) }))} /></label></div>}
    <div className="mt-5 flex justify-between"><Button variant="outline" disabled={step === 1} onClick={() => setStep(step - 1)}>Back</Button><Button type="button" disabled={step === 2 && !offerings.length} onClick={() => step < 3 ? setStep(step + 1) : setStep(4)}>{step === 3 ? 'Run Preflight' : 'Continue'}</Button></div>
  </CardContent></Card>}
  {step === 4 && <Card><CardHeader><CardTitle>Preflight</CardTitle></CardHeader><CardContent><p>Max solve time: {preferences.max_solve_seconds} seconds (CP-SAT only; preprocessing adds time)</p>{preflightResult && <PreflightDiagnostics result={preflightResult} />}{generationBusy && <p className="mt-3 rounded bg-blue-50 p-3 text-sm text-blue-900" role="status">Generation request is active for {generationElapsedSeconds}s. Backend logs report the current preparation, model, and solver stage; the solver limit does not cap preprocessing.</p>}<div className="mt-4 flex gap-2"><Button type="button" onClick={() => setStep(3)} variant="outline">Back</Button><Button type="button" onClick={() => void runPreflight()} disabled={preflightBusy || generationBusy}>{preflightBusy ? 'Checking...' : 'Run Preflight'}</Button>{preflightResult?.valid && <Button type="button" onClick={() => void generate()} disabled={preflightBusy || generationBusy}>{generationBusy ? `Generating... ${generationElapsedSeconds}s elapsed (solver ${preferences.max_solve_seconds}s)` : 'Generate Timetable'}</Button>}</div></CardContent></Card>}</main></AdminLayout>;
}
