'use client';

import { useState } from 'react';
import type { GenerationPreflightResult, GenerationPreflightError } from '@/lib/api/scheduling';

const entryLabel = (entry?: GenerationPreflightError['entry_1']) => entry ? `${entry.section} / ${entry.course}` : '—';

export function PreflightDiagnostics({ result }: { result: GenerationPreflightResult }) {
  const [showAll, setShowAll] = useState(false);
  const errors = result.errors ?? [];
  const count = (code: string) => errors.filter(error => error.code === code).length;
  const patterns = errors.filter(error => error.code === 'INVALID_SESSION_PATTERN');
  const clashes = errors.filter(error => error.code?.startsWith('LOCKED_'));
  const other = errors.filter(error => error.code !== 'INVALID_SESSION_PATTERN' && !error.code?.startsWith('LOCKED_'));
  const limit = showAll ? undefined : 20;
  const retainedLabel = result.mode === 'REBUILD_UNLOCKED' ? 'Locked' : 'Preserved';
  return <div className="mt-4 space-y-4 text-sm">
    <p className={result.valid ? 'font-semibold text-green-700' : 'font-semibold text-red-700'}>{result.valid ? 'Preflight passed' : 'Preflight failed'}</p>
    <p>Mode: {result.mode ?? 'Unknown'} · {retainedLabel} entries: {result.statistics?.preserved_entry_count ?? 0} · {retainedLabel} periods: {result.statistics?.preserved_period_count ?? 0} · Fully satisfied offerings skipped: {result.statistics?.fully_satisfied_offerings_skipped ?? 0} · Invalid session patterns: {patterns.length} · {retainedLabel} room conflicts: {count('LOCKED_ROOM_CLASH')} · {retainedLabel} faculty conflicts: {count('LOCKED_FACULTY_CLASH')} · {retainedLabel} section conflicts: {count('LOCKED_SECTION_CLASH')}</p>
    <p className="rounded bg-slate-50 p-3 text-slate-700">Physical periods required: {result.statistics?.physical_required_periods ?? 0} · Hybrid periods with placement-dependent delivery: {result.statistics?.hybrid_flexible_periods ?? 0} · Room periods available on configured hybrid offline days: {result.statistics?.hybrid_offline_room_capacity_periods ?? 0}</p>
    {patterns.length > 0 && <div className="overflow-x-auto"><h3 className="font-medium">Invalid session patterns</h3><table className="w-full border-collapse text-left text-xs"><thead><tr>{['Section', 'Course', 'Type', 'Weekly', 'Preserved', 'Remaining', 'Block', 'Problem'].map(label => <th className="border p-2" key={label}>{label}</th>)}</tr></thead><tbody>{patterns.slice(0, limit).map((error, index) => <tr key={`${error.course_offering_id}-${index}`}><td className="border p-2">{error.section}</td><td className="border p-2">{error.course_code} {error.course_name}</td><td className="border p-2">{error.activity_type}</td><td className="border p-2">{error.weekly_periods}</td><td className="border p-2">{error.locked_periods}</td><td className="border p-2">{error.remaining_periods}</td><td className="border p-2">{error.required_block_size}</td><td className="border p-2">{error.message} Pattern: {JSON.stringify(error.pattern)}</td></tr>)}</tbody></table></div>}
    {clashes.length > 0 && <div className="overflow-x-auto"><h3 className="font-medium">{retainedLabel} entry conflicts</h3><table className="w-full border-collapse text-left text-xs"><thead><tr>{['Type', 'Day', 'Time', 'Resource', 'Entry A', 'Entry B'].map(label => <th className="border p-2" key={label}>{label}</th>)}</tr></thead><tbody>{clashes.slice(0, limit).map((error, index) => <tr key={`${error.code}-${error.slot_id}-${index}`}><td className="border p-2">{error.code?.replace('LOCKED_', '').replace('_CLASH', '')}</td><td className="border p-2">{error.day}</td><td className="border p-2">{error.time_slot}</td><td className="border p-2">{error.resource}</td><td className="border p-2">{entryLabel(error.entry_1)}</td><td className="border p-2">{entryLabel(error.entry_2)}</td></tr>)}</tbody></table></div>}
    {other.slice(0, limit).map((error, index) => <p className="text-red-700" key={`${error.code}-${index}`}>{error.code}: {error.message}</p>)}
    {errors.length > 20 && <button type="button" className="text-blue-700 underline" onClick={() => setShowAll(value => !value)}>{showAll ? 'Show fewer errors' : `Show all ${errors.length} errors`}</button>}
  </div>;
}
