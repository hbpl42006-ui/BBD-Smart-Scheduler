/* eslint-disable @typescript-eslint/no-explicit-any */
'use client';

import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { fetchOptions } from '@/components/scheduling';
import { schedulingApi, ScheduleEntry, TimeSlot, Version } from '@/lib/api/scheduling';

type Room = {
  id: string; code?: string; building?: string; floor?: string; capacity?: number; room_type?: string;
  reserved_program_name?: string | null; reserved_year?: number | null; exclusive_reservation?: boolean;
};
type Avail = Room & { weekday?: number; time_slot?: string; status?: string; reason?: string };
type SectionOption = { id: string; name?: string; label?: string; year?: number; program_name?: string };
const roomReservation = (room: Room) => room.exclusive_reservation
  ? `Reserved: ${room.reserved_program_name || 'Program not assigned'} Year ${room.reserved_year ?? '?'}`
  : '';

export default function Page() {
  const [versions, setVersions] = useState<Version[]>([]);
  const [version, setVersion] = useState('');
  const [rooms, setRooms] = useState<Room[]>([]);
  const [slots, setSlots] = useState<TimeSlot[]>([]);
  const [sections, setSections] = useState<SectionOption[]>([]);
  const [entries, setEntries] = useState<ScheduleEntry[]>([]);
  const [availability, setAvailability] = useState<Avail[]>([]);
  const [selected, setSelected] = useState<{ room: Room; slot: TimeSlot; entry?: ScheduleEntry; status: string; reason?: string } | null>(null);
  const [sectionId, setSectionId] = useState('');
  const [form, setForm] = useState({ weekday: '0', start_slot: '', block_length: '1', capacity: '0', room_type: '' });
  const [free, setFree] = useState<Room[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const timetables = await schedulingApi.list();
        if (timetables[0]) {
          const listedVersions = await schedulingApi.listVersions(timetables[0].id);
          setVersions(listedVersions);
          if (listedVersions[0]) setVersion(listedVersions[0].id);
        }
        setSlots(await fetchOptions('/time-slots/'));
        setRooms(await fetchOptions('/rooms/'));
        setSections(await fetchOptions('/sections/'));
        setAvailability(await fetchOptions('/room-availabilities/'));
      } catch {
        setError('Unable to load room allocation data.');
      }
    })();
  }, []);

  useEffect(() => {
    if (version) schedulingApi.roomAllocation(version).then(setEntries).catch(() => setError('Unable to load allocation.'));
  }, [version]);

  const state = (room: Room, slot: TimeSlot) => {
    const entry = entries.find(item => (item.room_code === room.code || item.room === room.id)
      && item.weekday === Number(form.weekday) && item.start_slot === slot.id);
    if (entry) return { status: 'OCCUPIED', entry };
    const row = availability.find(item => item.id === room.id && item.weekday === Number(form.weekday) && item.time_slot === slot.id);
    return { status: row?.status || 'FREE', reason: row?.reason };
  };

  const find = async () => {
    setError(''); setFree([]);
    if (!version || !form.start_slot || !sectionId) {
      setError('Select a version, section, and start slot.');
      return;
    }
    setLoading(true);
    try {
      setFree(await schedulingApi.availableRooms(version, { ...form, section: sectionId }));
    } catch {
      setError('Unable to find available rooms.');
    } finally {
      setLoading(false);
    }
  };

  return <AdminLayout><Header title="Room Allocation"/><main className="space-y-5 p-5 sm:p-8">
    <Card><CardHeader><div className="flex flex-wrap justify-between gap-3"><CardTitle>Room × Time Slot</CardTitle><select className="rounded border p-2" value={version} onChange={event => setVersion(event.target.value)}><option value="">Select version</option>{versions.map(item => <option key={item.id} value={item.id}>v{item.version_no} · {item.status}</option>)}</select></div></CardHeader>
      <CardContent>{error && <p className="mb-3 rounded bg-red-50 p-3 text-red-700">{error}</p>}<div className="overflow-x-auto"><table className="min-w-[900px] w-full text-sm"><thead><tr><th className="sticky left-0 bg-white p-2 text-left">Room</th>{slots.map(slot => <th key={slot.id} className="border p-2">{slot.label || slot.name || `${slot.start_time}–${slot.end_time}`}</th>)}</tr></thead><tbody>
        {rooms.map(room => <tr key={room.id}><th className="sticky left-0 border bg-white p-2 text-left">{room.code}<div className="text-xs font-normal text-slate-500">{room.building} · {room.room_type}</div>{room.exclusive_reservation && <div className="text-xs font-semibold text-amber-700">{roomReservation(room)}</div>}</th>{slots.map(slot => { const cell = state(room, slot); return <td key={slot.id} className={`min-w-28 border p-2 text-center text-xs ${cell.status === 'OCCUPIED' ? 'bg-blue-100 text-blue-900' : cell.status === 'BLOCKED' ? 'bg-red-100 text-red-800' : cell.status === 'MAINTENANCE' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-50 text-emerald-700'}`}>{cell.status === 'FREE' ? 'FREE' : <button onClick={() => setSelected({ room, slot, entry: cell.entry, status: cell.status, reason: cell.reason })}>{cell.status === 'OCCUPIED' ? <><b>{cell.entry?.section_name || cell.entry?.section}</b><br/>{cell.entry?.course?.code || 'Course'}</> : cell.status}</button>}</td>; })}</tr>)}
      </tbody></table></div></CardContent>
    </Card>
    <Card><CardHeader><CardTitle>Find free room</CardTitle></CardHeader><CardContent className="flex flex-wrap items-end gap-3">
      <label className="text-sm">Section<select className="mt-1 block rounded border p-2" value={sectionId} onChange={event => { setFree([]); setSectionId(event.target.value); }}><option value="">Select section</option>{sections.map(section => <option key={section.id} value={section.id}>{section.program_name ? `${section.program_name} · ` : ''}{section.name || section.label}</option>)}</select></label>
      <label className="text-sm">Weekday<select className="mt-1 block rounded border p-2" value={form.weekday} onChange={event => { setFree([]); setForm({ ...form, weekday: event.target.value }); }}>{['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map((day, index) => <option value={index} key={day}>{day}</option>)}</select></label>
      <label className="text-sm">Start slot<select className="mt-1 block rounded border p-2" value={form.start_slot} onChange={event => { setFree([]); setForm({ ...form, start_slot: event.target.value }); }}><option value="">Select</option>{slots.map(slot => <option key={slot.id} value={slot.id}>{slot.label || slot.name || `${slot.start_time}–${slot.end_time}`}</option>)}</select></label>
      <label className="text-sm">Block length<input className="mt-1 block w-24 rounded border p-2" type="number" min="1" value={form.block_length} onChange={event => { setFree([]); setForm({ ...form, block_length: event.target.value }); }}/></label>
      <label className="text-sm">Minimum capacity<input className="mt-1 block w-28 rounded border p-2" type="number" min="0" value={form.capacity} onChange={event => { setFree([]); setForm({ ...form, capacity: event.target.value }); }}/></label>
      <Button onClick={find} disabled={loading}>{loading ? 'Finding…' : 'Find Available Rooms'}</Button>
      {sectionId && form.start_slot && !loading && free.length === 0 && <p className="w-full text-sm text-slate-500">No suitable rooms found for this section and time.</p>}
      {free.map(room => <div key={room.id} className="rounded border p-3 text-sm"><b>{room.code}</b> · {room.room_type}<div>{room.building}, floor {room.floor} · capacity {room.capacity}</div>{room.exclusive_reservation && <div className="font-semibold text-amber-700">{roomReservation(room)}</div>}</div>)}
    </CardContent></Card>
    {selected && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"><Card className="w-full max-w-lg"><CardHeader><CardTitle>{selected.status === 'OCCUPIED' ? 'Occupied room details' : `${selected.status} room details`}</CardTitle></CardHeader><CardContent className="space-y-2 text-sm"><p><b>Room:</b> {selected.room.code} · {selected.room.building}, floor {selected.room.floor}</p>{selected.room.exclusive_reservation && <p className="font-semibold text-amber-700">{roomReservation(selected.room)}</p>}<p><b>Type / capacity:</b> {selected.room.room_type || '—'} · {selected.room.capacity ?? '—'}</p><p><b>Status:</b> {selected.status}</p><p><b>Time slot:</b> {selected.slot.label || selected.slot.name || `${selected.slot.start_time}–${selected.slot.end_time}`}</p>{selected.reason && <p><b>Reason:</b> {selected.reason}</p>}{selected.entry && <><p><b>Section:</b> {selected.entry.section_name || selected.entry.section}</p><p><b>Course:</b> {selected.entry.course?.code || selected.entry.course_offering} — {selected.entry.course?.name || ''}</p><p><b>Entry type:</b> {selected.entry.entry_type} · <b>Block length:</b> {selected.entry.block_length} periods</p><p><b>Locked:</b> {selected.entry.locked ? 'Yes' : 'No'}</p><p><b>Faculty:</b> {(selected.entry as any).faculty_assignments?.map((item: any) => `${item.name || item.initials || item.faculty_id} — ${item.role}`).join(', ') || '—'}</p></>}<div className="flex justify-end"><Button variant="outline" onClick={() => setSelected(null)}>Close</Button></div></CardContent></Card></div>}
  </main></AdminLayout>;
}
