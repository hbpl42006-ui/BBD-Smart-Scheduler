'use client';

import { Button } from '@/components/ui/button';
import { FacultyTimetableResponse, ScheduleEntry } from '@/lib/api/scheduling';
import { getEntryPeriodCount, getWorkloadCellSpan } from '@/lib/faculty-workload-grid';

const DAYS = [['Monday', 0], ['Tuesday', 1], ['Wednesday', 2], ['Thursday', 3], ['Friday', 4]] as const;
const teachingPeriods = (entries: ScheduleEntry[], predicate: (entry: ScheduleEntry) => boolean) => entries.reduce((sum, entry) => sum + (predicate(entry) ? entry.block_length : 0), 0);

export function FacultyWorkloadGrid({ data, title = 'Individual Faculty Load' }: { data: FacultyTimetableResponse; title?: string }) {
  const entries = data.entries ?? [];
  const slots = [...(data.time_slots ?? [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const startsForDay = (day: number) => {
    const starts = new Map<string, ScheduleEntry[]>();
    for (const entry of entries) {
      if (entry.weekday !== day) continue;
      const group = starts.get(entry.start_slot) ?? [];
      group.push(entry);
      starts.set(entry.start_slot, group);
    }
    return starts;
  };
  const summary = data.summary;
  return <article className="workload-sheet">
    <header className="workload-heading"><h1>Babu Banarasi Das University</h1><h2>School of Engineering</h2><h3>{title}</h3><p>Session: {data.timetable?.academic_session ?? '-'}</p></header>
    <div className="workload-identity"><div><b>Faculty:</b> {data.faculty.name}</div><div><b>Employee Code:</b> {data.faculty.employee_code ?? '-'}</div><div><b>Department:</b> {data.faculty.department ?? '-'}</div></div>
    <div className="workload-scroll"><table className="workload-table"><thead><tr><th>Day / Time</th>{slots.map(slot => <th key={slot.id}>{slot.label}</th>)}</tr></thead><tbody>{DAYS.map(([label, day]) => {
      const startsBySlot = startsForDay(day);
      const renderedSlots = new Set<string>();
      return <tr key={label}><th>{label}</th>{slots.map((slot, index) => {
        if (renderedSlots.has(slot.id)) return null;
        const cellEntries = startsBySlot.get(slot.id) ?? [];
        const requestedSpan = cellEntries.length === 1 ? getEntryPeriodCount(cellEntries[0]) : 1;
        const span = cellEntries.length === 1 ? getWorkloadCellSpan(slots, index, requestedSpan, startsBySlot) : 1;
        for (const covered of slots.slice(index + 1, index + span)) renderedSlots.add(covered.id);
        return <td key={`${day}-${slot.id}`} colSpan={span} className={slot.is_break ? 'workload-break' : ''}>
          {slot.is_break && <div className="workload-break-label">LUNCH / BREAK</div>}
          {cellEntries.length > 1 && <strong className="workload-conflict">{cellEntries.length} overlapping classes</strong>}
          {slot.is_break && cellEntries.length > 0 && <strong className="workload-conflict">Scheduled during break</strong>}
          {cellEntries.map(entry => <div key={entry.id} className="workload-entry" title={`${entry.course?.name ?? ''} | ${entry.section_name ?? entry.section}`}><strong>{entry.course?.short_code || entry.course?.code || entry.course_offering}</strong><span>{entry.course?.name}</span><span>{entry.section_name ?? entry.section}</span><span>{entry.entry_type}</span><span>{entry.delivery_mode === 'ONLINE' ? 'ONLINE' : `OFFLINE · Room ${entry.room_code ?? 'pending'}`}</span>{getEntryPeriodCount(entry) > 1 && <span>{getEntryPeriodCount(entry)} Periods</span>}</div>)}
        </td>;
      })}</tr>;
    })}</tbody></table></div>
    <section className="workload-summary"><h4>Weekly Teaching Load</h4><div><span>Total Periods <b>{summary?.total_periods ?? teachingPeriods(entries, () => true)}</b></span><span>Lecture Periods <b>{summary?.lecture_periods ?? teachingPeriods(entries, entry => entry.entry_type === 'LECTURE')}</b></span><span>Practical Periods <b>{summary?.practical_periods ?? teachingPeriods(entries, entry => entry.entry_type === 'PRACTICAL')}</b></span><span>Online Periods <b>{summary?.online_periods ?? teachingPeriods(entries, entry => entry.delivery_mode === 'ONLINE')}</b></span><span>Offline Periods <b>{summary?.offline_periods ?? teachingPeriods(entries, entry => entry.delivery_mode === 'OFFLINE')}</b></span><span>Distinct Courses <b>{summary?.course_count ?? new Set(entries.map(entry => entry.course?.id ?? entry.course_offering)).size}</b></span><span>Distinct Sections <b>{summary?.section_count ?? new Set(entries.map(entry => entry.section)).size}</b></span></div></section>
  </article>;
}

export function WorkloadPrintButton() { return <Button className="no-print" onClick={() => window.print()}>Print Workload</Button>; }
