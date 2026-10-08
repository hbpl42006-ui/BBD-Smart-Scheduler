'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { getReceivedArrangementAttendance, ReceivedArrangementAttendance } from '@/lib/api/arrangements';

const dateLabel = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
const timeLabel = (value: string) => value.slice(0, 5);

export function ArrangementAttendanceSection() {
  const [items, setItems] = useState<ReceivedArrangementAttendance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    getReceivedArrangementAttendance()
      .then(response => { if (active) setItems(response.results); })
      .catch(() => { if (active) setError('Unable to load arrangement attendance.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  return <Card>
    <CardHeader className="flex flex-row items-center justify-between gap-3">
      <CardTitle>Arrangement Attendance</CardTitle>
      {!loading && !error && <span className="rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-800">{items.length} uploaded</span>}
    </CardHeader>
    <CardContent>
      {loading ? <p className="text-sm text-slate-500">Loading received attendance...</p>
        : error ? <p className="text-sm text-red-700">{error}</p>
          : !items.length ? <p className="text-sm text-slate-500">No arrangement attendance has been uploaded yet.</p>
            : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm">
              <thead><tr className="border-b text-slate-500"><th className="p-2">Date</th><th className="p-2">Time</th><th className="p-2">Course</th><th className="p-2">Section</th><th className="p-2">Arrangement Faculty</th><th className="p-2">Attendance</th><th className="p-2">Action</th></tr></thead>
              <tbody>{items.map(item => <tr className="border-b" key={item.id}>
                <td className="p-2">{dateLabel(item.date)}<div className="text-xs text-slate-500">Uploaded {new Date(item.evidence.uploaded_at).toLocaleString()}</div></td>
                <td className="p-2">{timeLabel(item.time_slot.start_time)}–{timeLabel(item.time_slot.end_time)}</td>
                <td className="p-2"><span className="font-medium">{item.course.code}</span><div className="text-xs text-slate-500">{item.course.name}</div></td>
                <td className="p-2">{item.section.name}</td><td className="p-2">{item.arrangement_faculty.name}</td>
                <td className="p-2">Uploaded</td>
                <td className="p-2"><Link className="font-medium text-blue-700 hover:underline" href={`/faculty-arrangements/attendance/${item.arrangement_id}/${item.evidence.id}`}>View Attendance</Link></td>
              </tr>)}</tbody>
            </table></div>}
    </CardContent>
  </Card>;
}
