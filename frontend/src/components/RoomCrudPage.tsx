'use client';

import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { PageHeader } from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { BulkImportDialog } from '@/components/BulkImportDialog';
import { me } from '@/lib/api/auth';
import { apiErrorMessage, clearListCache, create, listAll, update, type Entity } from '@/lib/api/resources';
import { canCreate, canEdit, canDeactivate, canImport, type Role } from '@/lib/permissions';

type RoomRecord = Entity & {
  code?: string; building?: string; floor?: string; capacity?: number; room_type?: string;
  room_type_display?: string; has_projector?: boolean; allowed_year?: number | null;
  reserved_program?: string | null; reserved_year?: number | null; exclusive_reservation?: boolean;
  active?: boolean;
};
type ProgramOption = { id: string; name: string; code: string };
const emptyRoom = {
  code: '', building: '', floor: '', capacity: 60, room_type: 'CLASSROOM', has_projector: false,
  allowed_year: null as number | null, reserved_program: '', reserved_year: null as number | null,
  exclusive_reservation: false,
};
const roomTypes = [
  ['CLASSROOM', 'Classroom'], ['COMPUTER_LAB', 'Computer Lab'], ['LAB', 'Lab'],
  ['SEMINAR_HALL', 'Seminar Hall'], ['AUDITORIUM', 'Auditorium'],
  ['ELECTRICAL_LAB', 'Electrical Lab'], ['WORKSHOP', 'Workshop'],
  ['MECHANICS_LAB', 'Mechanics Lab'], ['ENGINEERING_GRAPHICS_LAB', 'Engineering Graphics Lab'],
  ['PHYSICS_LAB', 'Physics Lab'], ['SUSTAINABLE_CHEMICAL_SCIENCES_LAB', 'Sustainable Chemical Sciences Lab'], ['QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB', 'Quantum Physics and Advanced Functional Materials Lab'],
  ['OTHER', 'Other'],
] as const;

export function RoomCrudPage() {
  const [rooms, setRooms] = useState<RoomRecord[]>([]);
  const [programs, setPrograms] = useState<ProgramOption[]>([]);
  const [role, setRole] = useState<Role>();
  const [editing, setEditing] = useState<RoomRecord | null>(null);
  const [form, setForm] = useState(emptyRoom);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [pendingRoom, setPendingRoom] = useState<RoomRecord | null>(null);
  const [mutatingRoomId, setMutatingRoomId] = useState<string | null>(null);
  const load = () => listAll<RoomRecord>('rooms').then(setRooms).catch(() => setError('Could not load rooms.'));

  useEffect(() => {
    void load();
    listAll<ProgramOption>('programs').then(setPrograms).catch(() => setPrograms([]));
    me().then(user => setRole(user.role as Role)).catch(() => setRole(undefined));
  }, []);

  const close = () => { setOpen(false); setEditing(null); setForm(emptyRoom); setError(''); };
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setError('');
    try {
      const payload = {
        ...form,
        allowed_year: form.allowed_year || null,
        reserved_year: form.exclusive_reservation ? form.reserved_year : null,
        reserved_program: form.exclusive_reservation ? (form.reserved_program || null) : null,
      };
      if (editing) await update('rooms', editing.id, payload);
      else await create('rooms', payload);
      close(); await load();
    } catch { setError('Could not save room. Check the room fields and try again.'); }
  };

  const startEdit = (room: RoomRecord) => {
    setEditing(room);
    setForm({
      code: room.code || '', building: room.building || '', floor: room.floor || '',
      capacity: room.capacity || 0, room_type: room.room_type || 'CLASSROOM',
      has_projector: !!room.has_projector, allowed_year: room.allowed_year ?? null,
      reserved_program: room.reserved_program || '', reserved_year: room.reserved_year ?? null,
      exclusive_reservation: !!room.exclusive_reservation,
    });
    setOpen(true);
  };

  const setRoomActive = async (room: RoomRecord, active: boolean) => {
    setError(''); setMutatingRoomId(room.id);
    try {
      const updated = await update<RoomRecord>('rooms', room.id, { active });
      clearListCache('rooms');
      setRooms(current => current.map(item => item.id === room.id ? { ...item, ...updated, active } : item));
      setPendingRoom(null);
    } catch (mutationError) {
      setError(apiErrorMessage(mutationError, `Unable to ${active ? 'activate' : 'deactivate'} room ${room.code || ''}.`));
    } finally {
      setMutatingRoomId(null);
    }
  };

  return <AdminLayout><Header title="Rooms & Labs"/><main className="mx-auto w-full max-w-[1500px] space-y-4 p-5 sm:p-8">
    <PageHeader title="Rooms & Labs" description="Manage teaching spaces, year eligibility, and exclusive program reservations."/>
    <div className="flex flex-wrap gap-2">{canCreate(role) && <Button onClick={() => { setEditing(null); setForm(emptyRoom); setOpen(true); }}>Add Room</Button>}{canImport(role) && <BulkImportDialog kind="rooms" onComplete={() => void load()}/>}</div>
    {error && !open && <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <Card><CardContent className="overflow-x-auto p-0"><table className="w-full text-left text-sm"><thead className="border-b bg-slate-50"><tr>{['Room No.','Building','Floor','Capacity','Room type','Projector','Allowed Year','Reservation','Status','Actions'].map(label => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>
      {rooms.map(room => <tr className="border-b" key={room.id}><td className="p-3">{room.code}</td><td className="p-3">{room.building}</td><td className="p-3">{room.floor}</td><td className="p-3">{room.capacity}</td><td className="p-3">{room.room_type_display || roomTypes.find(([value]) => value === room.room_type)?.[1] || room.room_type || '—'}</td><td className="p-3">{room.has_projector ? 'Yes' : 'No'}</td><td className="p-3">{room.allowed_year ? `Year ${room.allowed_year} Only` : 'All Years'}</td><td className="p-3">{room.exclusive_reservation ? `Reserved: ${programs.find(program => program.id === room.reserved_program)?.name || 'Program not assigned'} Year ${room.reserved_year ?? '?'}` : '—'}</td><td className="p-3">{room.active ? 'Active' : 'Inactive'}</td><td className="p-3">{canEdit(role) && <Button variant="outline" size="sm" onClick={() => startEdit(room)}>Edit</Button>}{canDeactivate(role) && <Button className="ml-2" variant="outline" size="sm" disabled={mutatingRoomId === room.id} onClick={() => room.active ? setPendingRoom(room) : void setRoomActive(room, true)}>{mutatingRoomId === room.id ? (room.active ? 'Deactivating…' : 'Activating…') : room.active ? 'Deactivate' : 'Activate'}</Button>}</td></tr>)}
    </tbody></table>{rooms.length === 0 && <p className="p-6 text-sm text-slate-500">No rooms have been added yet.</p>}</CardContent></Card>
    {open && <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4"><form onSubmit={save} className="max-h-[90vh] w-full max-w-lg space-y-3 overflow-auto rounded-xl bg-white p-6"><h2 className="text-lg font-semibold">{editing ? 'Edit' : 'Add'} Room</h2>{error && <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      {([['code','Room No.'],['building','Building'],['floor','Floor']] as const).map(([key,label]) => <label className="block text-sm" key={key}>{label}<Input className="mt-1" required value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })}/></label>)}
      <label className="block text-sm">Capacity<Input className="mt-1" required min={1} type="number" value={form.capacity} onChange={event => setForm({ ...form, capacity: Number(event.target.value) })}/></label>
      <label className="block text-sm">Room type<select className="mt-1 h-10 w-full rounded border px-2" value={form.room_type} onChange={event => setForm({ ...form, room_type: event.target.value })}>{roomTypes.map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="block text-sm">Projector<select className="mt-1 h-10 w-full rounded border px-2" value={String(form.has_projector)} onChange={event => setForm({ ...form, has_projector: event.target.value === 'true' })}><option value="false">No</option><option value="true">Yes</option></select></label>
      <label className="block text-sm">Allowed Year<select className="mt-1 h-10 w-full rounded border px-2" value={form.allowed_year ?? ''} onChange={event => setForm({ ...form, allowed_year: event.target.value ? Number(event.target.value) : null })}><option value="">All Years</option>{[1,2,3,4].map(year => <option key={year} value={year}>Year {year}</option>)}</select></label>
      <label className="block text-sm">Exclusive program/year reservation<select className="mt-1 h-10 w-full rounded border px-2" value={String(form.exclusive_reservation)} onChange={event => setForm({ ...form, exclusive_reservation: event.target.value === 'true' })}><option value="false">No</option><option value="true">Yes</option></select></label>
      {form.exclusive_reservation && <><label className="block text-sm">Reserved Program<select required className="mt-1 h-10 w-full rounded border px-2" value={form.reserved_program} onChange={event => setForm({ ...form, reserved_program: event.target.value })}><option value="">Select program</option>{programs.map(program => <option key={program.id} value={program.id}>{program.name}</option>)}</select></label><label className="block text-sm">Reserved Year<select required className="mt-1 h-10 w-full rounded border px-2" value={form.reserved_year ?? ''} onChange={event => setForm({ ...form, reserved_year: event.target.value ? Number(event.target.value) : null })}><option value="">Select year</option>{[1,2,3,4].map(year => <option key={year} value={year}>Year {year}</option>)}</select></label></>}
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={close}>Cancel</Button><Button type="submit">Save</Button></div>
    </form></div>}
    {pendingRoom && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"><section role="dialog" aria-modal="true" aria-labelledby="room-deactivate-title" className="w-full max-w-md space-y-4 rounded-xl bg-white p-6 shadow-xl"><h2 id="room-deactivate-title" className="text-lg font-semibold">Deactivate room {pendingRoom.code}?</h2><p className="text-sm text-slate-600">This room will no longer be available for new timetable generation or room allocation. Existing timetable history will remain unchanged.</p>{error && <p role="alert" className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}<div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={mutatingRoomId === pendingRoom.id} onClick={() => { setPendingRoom(null); setError(''); }}>Cancel</Button><Button type="button" disabled={mutatingRoomId === pendingRoom.id} onClick={() => void setRoomActive(pendingRoom, false)}>{mutatingRoomId === pendingRoom.id ? 'Deactivating…' : 'Deactivate'}</Button></div></section></div>}
  </main></AdminLayout>;
}
