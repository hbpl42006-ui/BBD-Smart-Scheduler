'use client';

type ReferenceFaculty = { name?: string; initials?: string };
type OfficialEntry = {
  id: string;
  course_offering: string;
  weekday: number;
  start_slot: string;
  block_length: number;
  room_code?: string | null;
  delivery_mode?: string;
  entry_type: string;
  locked?: boolean;
  course?: { code?: string; name?: string; short_code?: string; credit?: number | string | null };
  faculty_assignments?: ReferenceFaculty[];
};
type OfficialSlot = { id: string; label: string; order: number; is_break: boolean; start_time: string; end_time: string };
type OfficialSection = {
  name: string;
  year: number;
  weekly_off_day?: number | null;
  program: { name: string; code?: string };
  department: { name: string };
  institution: { name: string };
  semester: { name: string; number?: number; type?: string };
  session: { name: string };
  coordinator: { name: string; mobile: string };
};

export type OfficialTimetableData = { section: OfficialSection; version: { id: string; status: string; version_no: number } | null; time_slots: OfficialSlot[]; entries: OfficialEntry[] };

const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
const weekdaysFull = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
const ordinal = (n: number) => ({ 1: 'First', 2: 'Second', 3: 'Third', 4: 'Fourth', 5: 'Fifth' }[n] ?? `${n}th`);
const roman = (n: number) => ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII'][n] ?? String(n);
const typePrefix = (type: string) => type === 'LECTURE' ? 'L' : ['PRACTICAL', 'LAB'].includes(type) ? 'P' : type === 'LIBRARY' ? 'LIB' : type === 'TUTORIAL' ? 'T' : type;
const timeLabel = (slot: OfficialSlot) => {
  const hour = (value: string) => {
    const n = Number(value.split(':')[0]);
    return n > 12 ? String(n - 12) : String(n).padStart(2, '0');
  };
  return `${hour(slot.start_time)} to ${hour(slot.end_time)}`;
};
const metadata = (entry: OfficialEntry) => {
  if (entry.entry_type === 'LIBRARY') return 'LIB';
  const initials = [...new Set((entry.faculty_assignments ?? []).map(x => x.initials?.trim()).filter(Boolean))].join('+');
  const parts = [typePrefix(entry.entry_type), entry.course?.short_code || entry.course?.code, initials].filter(Boolean);
  if (entry.delivery_mode !== 'ONLINE' && entry.room_code) parts.push(entry.room_code);
  return parts.join('/');
};

export function OfficialSectionTimetable({ data }: { data: OfficialTimetableData }) {
  const { section, time_slots: slots, entries, version } = data;
  const semesterNo = section.semester.number ?? (section.semester.type === 'EVEN' ? section.year * 2 : section.year * 2 - 1);
  const academicHeading = section.program.code?.startsWith('MTECH') || section.program.name.startsWith('M.Tech')
    ? `${section.program.name} - ${roman(section.semester.number ?? 1)} Semester`
    : `${section.program.name} ${ordinal(section.year)} Year, ${section.semester.name}`;
  const usedSlots = slots;
  const byCell = new Map<string, OfficialEntry[]>();
  for (const entry of entries) {
    const key = `${entry.weekday}:${entry.start_slot}`;
    byCell.set(key, [...(byCell.get(key) ?? []), entry]);
  }
  const courses = new Map<string, OfficialEntry[]>();
  for (const entry of entries) courses.set(entry.course_offering, [...(courses.get(entry.course_offering) ?? []), entry]);
  const covered = (weekday: number) => {
    const ids = new Set<string>();
    for (const entry of entries.filter(item => item.weekday === weekday)) {
      const index = slots.findIndex(slot => slot.id === entry.start_slot);
      for (let offset = 1; offset < entry.block_length; offset += 1) {
        const slot = slots[index + offset];
        if (slot && !slot.is_break) ids.add(slot.id);
      }
    }
    return ids;
  };
  const referenceRows = [...courses.values()].map(group => {
    const entry = group[0];
    const distinctMetadata = [...new Set(group.map(metadata).filter(Boolean))].join('; ');
    const faculty = [...new Set(group.flatMap(item => (item.faculty_assignments ?? []).map(person => person.name).filter(Boolean)))].join(', ');
    return { key: entry.course_offering, entry, distinctMetadata, faculty };
  });

  return <article className="official-sheet" data-testid="official-section-timetable">
    <header className="official-heading">
      <h1>{section.institution.name || 'Babu Banarasi Das University'}</h1>
      <h2>School of Engineering</h2>
      <h3>{section.department.name}</h3>
      <p>{academicHeading}</p>
      <p>Academic Session: {section.session.name}</p>
    </header>
    {version && <div className="official-status">{version.status.replaceAll('_', ' ')} · Version {version.version_no}</div>}
    <div className="official-grid-scroll"><table className="official-table">
      <thead><tr><th className="section-label"/><th className="day-label">Time / Day</th>{usedSlots.map(slot => <th className={slot.is_break ? 'lunch-heading' : ''} key={slot.id}>{slot.is_break ? '' : timeLabel(slot)}</th>)}</tr></thead>
      <tbody>{weekdaysFull.map((day, weekday) => {
        const coveredSlots = covered(weekday);
        const isWeeklyOffDay = section.weekly_off_day === weekday;
        return <tr key={day} className={isWeeklyOffDay ? 'official-off-day-row' : undefined}>
          {weekday === 0 && <td className="section-label" rowSpan={5}>{section.program.name} - {roman(semesterNo)} Sem<br/>Section: {section.name}</td>}
          <th className="day-label">{days[weekday]}{isWeeklyOffDay && <span className="official-off-day-label"> OFF DAY</span>}</th>
          {usedSlots.map((slot, index) => {
            if (coveredSlots.has(slot.id)) return null;
            if (slot.is_break) return <td className="lunch-cell" key={slot.id}>{['L', 'U', 'N', 'C', 'H'][weekday]}</td>;
            const occupying = byCell.get(`${weekday}:${slot.id}`) ?? [];
            const entry = occupying[0];
            const span = entry ? Math.max(1, Math.min(entry.block_length, usedSlots.slice(index).filter(item => !item.is_break).length)) : 1;
            return <td className="official-entry-cell" colSpan={span} key={slot.id}>
              {occupying.length > 1 ? occupying.map(item => <div className="compact-entry" key={item.id}>{metadata(item)}</div>) : entry ? <span className="compact-entry">{metadata(entry)}{entry.locked && <span className="official-lock" title="Locked entry"> ▣</span>}</span> : null}
            </td>;
          })}
        </tr>;
      })}</tbody>
    </table></div>
    <div className="official-coordinator"><span>Class Coordinator: {section.coordinator.name || 'Not Assigned'}</span><span>Mobile No.: {section.coordinator.mobile || '-'}</span></div>
    <table className="course-table"><thead><tr><th>Credit</th><th>Codes</th><th>Course Name</th><th>Meta Data</th><th>Faculty Name</th></tr></thead>
      <tbody>{referenceRows.map(({ key, entry, distinctMetadata, faculty }) => <tr key={key}>
        <td>{entry.course?.credit ?? '-'}</td><td>{entry.course?.code ?? '-'}</td><td>{entry.course?.name ?? '-'}</td><td>{distinctMetadata || '-'}</td><td>{faculty || '-'}</td>
      </tr>)}</tbody>
    </table>
    <p className="official-legend">No Room No. = ONLINE &nbsp; | &nbsp; Room No. shown = OFFLINE / Physical Class</p>
    <footer className="signature-footer"><div>____________________________<br/>Head / HOD<br/>{section.department.name}</div><div>____________________________<br/>Coordinator - Academic Activities</div><div>____________________________<br/>Dean<br/>School of Engineering</div></footer>
  </article>;
}
