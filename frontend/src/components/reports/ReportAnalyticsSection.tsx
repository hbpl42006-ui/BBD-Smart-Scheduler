'use client';

type Row = Record<string, unknown>;
type Metric = { label: string; value: string | number };
type Bar = { label: string; value: number; suffix?: string };

const numberValue = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

function Bars({ title, data }: { title: string; data: Bar[] }) {
  const max = Math.max(...data.map(item => item.value), 1);
  return <div className="rounded-lg border bg-white p-4">
    <h3 className="mb-3 text-sm font-semibold capitalize text-slate-700">{title}</h3>
    <div className="space-y-2">
      {data.slice(0, 12).map(item => <div key={item.label} className="grid grid-cols-[minmax(90px,1fr)_2fr_auto] items-center gap-2 text-xs">
        <span className="truncate text-slate-600" title={item.label}>{item.label}</span>
        <div className="h-2 rounded bg-slate-100"><div className="h-2 rounded bg-indigo-500" style={{ width: `${Math.max((item.value / max) * 100, item.value ? 3 : 0)}%` }} /></div>
        <span className="tabular-nums text-slate-700">{item.value}{item.suffix ?? ''}</span>
      </div>)}
      {!data.length && <p className="text-xs text-slate-400">No data for the selected filters.</p>}
    </div>
  </div>;
}

const grouped = (rows: Row[], key: string, numericKey?: string): Bar[] => {
  const values = new Map<string, number>();
  rows.forEach(row => {
    const label = String(row[key] ?? 'Unknown');
    values.set(label, (values.get(label) ?? 0) + (numericKey ? numberValue(row[numericKey]) : 1));
  });
  return [...values.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
};

function freeRooms(rows: Row[]) {
  const capacity = rows.map(row => numberValue(row.capacity));
  return { metrics: [
    { label: 'Free Rooms', value: rows.length },
    { label: 'Projector Available', value: rows.filter(row => String(row.projector).toLowerCase() === 'yes').length },
    { label: 'Average Capacity', value: capacity.length ? Math.round(capacity.reduce((a, b) => a + b, 0) / capacity.length) : 0 },
    { label: 'Largest Free Room Capacity', value: capacity.length ? Math.max(...capacity) : 0 },
  ], charts: [
    ['Free Rooms by Room Type', grouped(rows, 'room_type')],
    ['Projector Availability', [{ label: 'Available', value: rows.filter(row => String(row.projector).toLowerCase() === 'yes').length }, { label: 'Not Available', value: rows.filter(row => String(row.projector).toLowerCase() !== 'yes').length }]],
    ['Free Rooms by Building', grouped(rows, 'building')],
    ['Capacity Distribution', [0, 30, 50, 70].map((min, index) => { const max = [30, 50, 70, Infinity][index]; return { label: index === 0 ? '0-30' : index === 1 ? '31-50' : index === 2 ? '51-70' : '71+', value: rows.filter(row => numberValue(row.capacity) > min && numberValue(row.capacity) <= max).length }; })],
  ] as [string, Bar[]][] };
}

function roomUtilization(rows: Row[]) {
  const used = rows.filter(row => numberValue(row.occupied_slots) > 0);
  return { metrics: [
    { label: 'Total Rooms', value: rows.length }, { label: 'Rooms Used', value: used.length },
    { label: 'Average Utilization', value: `${rows.length ? Math.round(rows.reduce((a, r) => a + numberValue(r.utilization_percentage), 0) / rows.length) : 0}%` },
    { label: 'Highest Utilized Room', value: String(used.sort((a, b) => numberValue(b.utilization_percentage) - numberValue(a.utilization_percentage))[0]?.room_no ?? '—') },
  ], charts: [['Room Utilization %', rows.map(row => ({ label: String(row.room_no ?? ''), value: numberValue(row.utilization_percentage), suffix: '%' }))], ['Building-wise utilization', grouped(rows, 'building', 'utilization_percentage')], ['Room Type Utilization', grouped(rows, 'room_type', 'utilization_percentage')]] as [string, Bar[]][] };
}

function workload(rows: Row[]) { const values = rows.map(row => numberValue(row.total_scheduled_periods)); return { metrics: [{ label: 'Faculty with Published Classes', value: rows.length }, { label: 'Total Teaching Periods', value: values.reduce((a, b) => a + b, 0) }, { label: 'Average Teaching Periods', value: rows.length ? Math.round(values.reduce((a, b) => a + b, 0) / rows.length) : 0 }, { label: 'Maximum Teaching Load', value: values.length ? Math.max(...values) : 0 }], charts: [['Faculty vs Teaching Periods', rows.map(row => ({ label: String(row.faculty_name ?? ''), value: numberValue(row.total_scheduled_periods) }))], ['Courses per Faculty', grouped(rows, 'faculty_name', 'distinct_courses')], ['Sections per Faculty', grouped(rows, 'faculty_name', 'distinct_sections')]] as [string, Bar[]][] }; }

function allocation(rows: Row[], unscheduled: boolean) { const required = rows.reduce((a, r) => a + numberValue(r.required_periods), 0); const scheduled = rows.reduce((a, r) => a + numberValue(r.scheduled_periods), 0); const remaining = rows.reduce((a, r) => a + Math.max(numberValue(r.required_periods) - numberValue(r.scheduled_periods), 0), 0); return { metrics: [{ label: unscheduled ? 'Incomplete Offerings' : 'Active Offerings', value: rows.length }, { label: unscheduled ? 'Remaining Periods' : 'Required Periods', value: unscheduled ? remaining : required }, { label: 'Scheduled Periods', value: scheduled }, { label: 'Remaining Periods', value: remaining }], charts: [['Required vs Scheduled', [{ label: 'Required', value: required }, { label: 'Scheduled', value: scheduled }]], ['Remaining Periods by Section', grouped(rows, 'program', 'difference')], ['Offering Distribution by Class Type', grouped(rows, 'entry_type')]] as [string, Bar[]][] }; }

export function ReportAnalyticsSection({ report, rows }: { report: string; rows: Row[] }) {
  let result: { metrics: Metric[]; charts: [string, Bar[]][] } | null = null;
  if (report === 'free-rooms') result = freeRooms(rows);
  else if (report === 'room-utilization') result = roomUtilization(rows);
  else if (report === 'faculty-workload') result = workload(rows);
  else if (report === 'course-allocation' || report === 'unscheduled') result = allocation(rows, report === 'unscheduled');
  if (!result) return null;
  return <section className="no-print space-y-4" aria-label={`${report} analytics`}>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{result.metrics.map(metric => <div className="rounded-lg border bg-white p-4" key={metric.label}><p className="text-xs text-slate-500">{metric.label}</p><p className="mt-1 text-2xl font-semibold text-slate-900">{metric.value}</p></div>)}</div>
    <div className="grid gap-4 md:grid-cols-2">{result.charts.map(([title, data]) => <Bars title={title} data={data} key={title} />)}</div>
  </section>;
}
