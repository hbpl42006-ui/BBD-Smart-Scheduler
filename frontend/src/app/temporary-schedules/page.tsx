'use client';

import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { apiClient } from '@/lib/api/client';

type Item = {
  id: string; name: string; title: string; code: string; label: string; is_break: boolean;
  status: string; display_status: string; event_type: string; notes: string; start_date: string;
  end_date: string; published_at: string | null; base_timetable_version: string; priority: number;
  version_no: number; sections: string[]; blocks: Item[]; created_by: string;
  affected_regular_periods: number | null; program_id: string; program_name: string;
  year: number; employee_code: string; section: string; specific_date: string | null;
  weekday: number; start_slot: string; block_length: number; summary: Record<string, unknown>;
  conflicts: Item[]; warnings: Item[]; temporary_events: Item[]; overridden_classes: Item[];
  date: string; message: string; start_time: string; end_time: string; room: string;
  faculty: string[]; trainer_name: string; delivery_mode: string; override_regular_class: boolean; order: number;
  room_type_label: string; building: string; floor: string; capacity: number; has_projector: boolean;
};
type PatternDraft = {
  id: string; mode: 'weekday' | 'date'; weekdays: number[]; specificDate: string;
  startSlot: string; endSlot: string; override: boolean;
};
const eventTypes = ['TRAINING','WORKSHOP','PLACEMENT','SEMINAR','GUEST_LECTURE','EXAM_PREPARATION','SPECIAL_CLASS','OTHER'];
const weekdays = ['Monday','Tuesday','Wednesday','Thursday','Friday'];
const emptyPattern = (): PatternDraft => ({id: `${Date.now()}-${Math.random()}`, mode: 'weekday', weekdays: [0,1,2,3,4], specificDate: '', startSlot: '', endSlot: '', override: true});
const rows = (data: unknown): Item[] => Array.isArray(data) ? data as Item[] : data && typeof data === 'object' && 'results' in data && Array.isArray(data.results) ? data.results as Item[] : [];
const clockLabel = (value: string) => {
  const [h, m] = value.split(':').map(Number);
  return `${String(h % 12 || 12).padStart(2, '0')}:${String(m || 0).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
};

export default function TemporarySchedulesPage() {
  const [versions,setVersions]=useState<Item[]>([]),[programs,setPrograms]=useState<Item[]>([]),[sections,setSections]=useState<Item[]>([]);
  const [slots,setSlots]=useState<Item[]>([]),[rooms,setRooms]=useState<Item[]>([]),[faculty,setFaculty]=useState<Item[]>([]),[plans,setPlans]=useState<Item[]>([]);
  const [roomsLoading,setRoomsLoading]=useState(true),[roomLoadError,setRoomLoadError]=useState(false),[facultyLoading,setFacultyLoading]=useState(true),[roomSearch,setRoomSearch]=useState('');
  const [plan,setPlan]=useState<Item|null>(null),[impact,setImpact]=useState<Item|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[canPublish,setCanPublish]=useState(false);
  const [title,setTitle]=useState(''),[type,setType]=useState('TRAINING'),[notes,setNotes]=useState(''),[start,setStart]=useState(''),[end,setEnd]=useState(''),[priority,setPriority]=useState(0),[version,setVersion]=useState('');
  const [program,setProgram]=useState(''),[year,setYear]=useState(''),[sectionSearch,setSectionSearch]=useState(''),[selectedSections,setSelectedSections]=useState<string[]>([]);
  const [patterns,setPatterns]=useState<PatternDraft[]>([emptyPattern()]),[roomAssignment,setRoomAssignment]=useState<'NONE'|'SHARED'|'LATER'>('LATER');
  const [patternMode,setPatternMode]=useState<'date'|'weekday'>('weekday'),[specificDate,setSpecificDate]=useState(''),[weekday,setWeekday]=useState(0),[slot,setSlot]=useState(''),[length,setLength]=useState(1),[room,setRoom]=useState(''),[trainerIds,setTrainerIds]=useState<string[]>([]),[trainerName,setTrainerName]=useState(''),[delivery,setDelivery]=useState('OFFLINE'),[override,setOverride]=useState(false);

  const load = async () => { const {data}=await apiClient.get('/temporary-schedules/'); setPlans(rows(data)); };
  useEffect(()=>{
    Promise.all([apiClient.get('/temporary-schedules/options/'),apiClient.get('/programs/')])
      .then(([v,p])=>{setVersions(v.data.versions??[]);setCanPublish(Boolean(v.data.can_publish));setPrograms(rows(p.data));})
      .catch(e=>{console.error('Temporary schedule setup load failed',e);setError('Unable to load schedule setup. Check the API response and server log.');});
    apiClient.get('/temporary-schedules/room-options/').then(({data})=>setRooms(rows(data)))
      .catch(e=>{console.error('Temporary room options failed',e);setRoomLoadError(true);}).finally(()=>setRoomsLoading(false));
    apiClient.get('/temporary-schedules/faculty-options/').then(({data})=>setFaculty(rows(data)))
      .catch(e=>{console.error('Temporary faculty options failed',e);setError('Unable to load faculty options.');}).finally(()=>setFacultyLoading(false));
    apiClient.get('/temporary-schedules/').then(({data})=>setPlans(rows(data))).catch(e=>{console.error('Temporary plan list failed',e);setError('Unable to load temporary plans.');});
  },[]);
  useEffect(()=>{
    apiClient.get('/temporary-schedules/section-options/',{params:version?{version}:{}})
      .then(({data})=>setSections(rows(data))).catch(e=>{console.error('Temporary section options failed',e);setError('Unable to load section options.');});
    if(!version){setSlots([]);return;}
    apiClient.get('/temporary-schedules/slot-options/',{params:{version}})
      .then(({data})=>setSlots(rows(data))).catch(e=>{console.error('Canonical timetable slots failed',e);setError(axios.isAxiosError(e)&&e.response?.data?.detail?e.response.data.detail:'Unable to load canonical time slots for this published version.');});
  },[version]);

  const filteredSections=useMemo(()=>sections.filter(s=>(!program||String(s.program_id)===program)&&(!year||String(s.year)===year)&&(!sectionSearch||`${s.name} ${s.program_name} ${s.year}`.toLowerCase().includes(sectionSearch.trim().toLowerCase()))),[sections,program,year,sectionSearch]);
  const filteredRooms=useMemo(()=>rooms.filter(r=>`${r.code} ${r.room_type_label} ${r.building} ${r.floor}`.toLowerCase().includes(roomSearch.trim().toLowerCase())),[rooms,roomSearch]);
  const filteredIds=useMemo(()=>filteredSections.map(s=>String(s.id)),[filteredSections]);
  const grouped=useMemo(()=>filteredSections.reduce<Record<string,Item[]>>((acc,s)=>{const key=`${s.program_name||'Program'} · Year ${s.year}`;(acc[key]??=[]).push(s);return acc},{}),[filteredSections]);
  const allFilteredSelected=filteredIds.length>0&&filteredIds.every(id=>selectedSections.includes(id));
  const patternError=(p:PatternDraft)=>{
    if(p.mode==='date'&&(!p.specificDate||p.specificDate<start||p.specificDate>end))return 'Choose a specific date within the plan date range.';
    if(p.mode==='weekday'&&!p.weekdays.length)return 'Choose at least one weekday.';
    if(!p.startSlot||!p.endSlot)return 'Choose a start and end time.';
    const first=slots.findIndex(s=>String(s.id)===p.startSlot),last=slots.findIndex(s=>String(s.id)===p.endSlot);
    if(first<0||last<first)return 'End time must be after start time.';
    const window=slots.slice(first,last+1),breakSlot=window.find(s=>s.is_break);
    if(breakSlot)return `Selected range crosses ${breakSlot.label} (break). Split the pattern around this break.`;
    if(window.some((s,i)=>i>0&&window[i-1].end_time!==s.start_time))return 'Selected range crosses a gap in the timetable.';
    return '';
  };
  const patternsValid=patterns.length>0&&patterns.every(p=>!patternError(p));
  const formErrors=[!title.trim()?'Enter an event title.':'',!version?'Choose a published timetable version.':'',!start||!end||start>end?'Choose a valid date range.':'',!selectedSections.length?'Select at least one section.':'',!patternsValid?'Add at least one valid schedule pattern.':''].filter(Boolean);
  const updatePattern=(id:string,changes:Partial<PatternDraft>)=>setPatterns(old=>old.map(p=>p.id===id?{...p,...changes}:p));
  const run=async(action:()=>Promise<void>)=>{
    setBusy(true);setError('');
    try{await action();}
    catch(e:unknown){
      const payload=axios.isAxiosError(e)?e.response?.data as {detail?:string;conflicts?:Item[];[key:string]:unknown}|undefined:undefined;
      const fieldError=payload?Object.entries(payload).filter(([key])=>key!=='conflicts').flatMap(([,v])=>Array.isArray(v)?v.map(String):typeof v==='string'?[v]:[])[0]:undefined;
      const status=axios.isAxiosError(e)?e.response?.status:undefined;
      if(status&&status>=500)console.error('Temporary schedule API server error',e);
      setError(fieldError??(status&&status>=500?'The server could not complete this request. The API error was logged; check the Django traceback.':e instanceof Error?e.message:'The request failed.'));
      if(payload?.conflicts)setImpact(payload as unknown as Item);
    }finally{setBusy(false);}
  };
  const selectPlan=(item:Item)=>{setPlan(item);setImpact(null);setTitle(item.title);setType(item.event_type);setNotes(item.notes??'');setStart(item.start_date);setEnd(item.end_date);setPriority(item.priority??0);setVersion(item.base_timetable_version);setSelectedSections((item.sections??[]).map(String));};
  const save=()=>run(async()=>{
    const payload={title,event_type:type,notes,start_date:start,end_date:end,priority,sections:selectedSections};
    const {data}=plan?.status==='DRAFT'?await apiClient.patch(`/temporary-schedules/${plan.id}/`,payload):await apiClient.post('/temporary-schedules/',{
      ...payload,base_timetable_version:version,
      patterns:patterns.map(p=>({mode:p.mode,weekdays:p.weekdays,specific_date:p.specificDate,start_slot:p.startSlot,end_slot:p.endSlot,override_regular_class:p.override,room_id:roomAssignment==='SHARED'?room||null:null,delivery_mode:delivery,faculty:trainerIds,trainer_name:trainerName})),
    });
    selectPlan(data);await load();
  });
  const addPattern=()=>run(async()=>{
    if(!plan||plan.status!=='DRAFT')return;
    for(const sectionId of selectedSections)await apiClient.post(`/temporary-schedules/${plan.id}/blocks/`,{section:sectionId,specific_date:patternMode==='date'?specificDate:null,weekday:patternMode==='weekday'?weekday:null,start_slot:slot,block_length:length,event_type:type,title,room:room||null,faculty:trainerIds,trainer_name:trainerName,delivery_mode:delivery,override_regular_class:override});
    const {data}=await apiClient.get(`/temporary-schedules/${plan.id}/`);selectPlan(data);await load();
  });
  const removeBlock=(id:string)=>run(async()=>{await apiClient.delete(`/temporary-schedules/${plan?.id}/blocks/${id}/`);const {data}=await apiClient.get(`/temporary-schedules/${plan?.id}/`);selectPlan(data);});
  const review=(action:'preview'|'validate'|'publish'|'cancel')=>run(async()=>{const {data}=await apiClient.post(`/temporary-schedules/${plan?.id}/${action}/`,{});if(action==='preview'||action==='validate')setImpact(data);else{selectPlan(data);await load();}});
  const deletePlan=()=>run(async()=>{await apiClient.delete(`/temporary-schedules/${plan?.id}/`);setPlan(null);setImpact(null);await load();});
  const newPlan=()=>{setPlan(null);setImpact(null);setTitle('');setType('TRAINING');setNotes('');setStart('');setEnd('');setPriority(0);setVersion('');setSelectedSections([]);setPatterns([emptyPattern()]);setRoomAssignment('LATER');setRoom('');setTrainerIds([]);setTrainerName('');};

  return <AdminLayout><Header title="Temporary / Special Schedules"/><main className="mx-auto w-full max-w-6xl space-y-6 p-5 sm:p-8">
    <div><h1 className="text-2xl font-semibold">Temporary / Special Schedules</h1><p className="text-sm text-slate-500">Create date-bounded events on a published timetable. Regular classes return automatically after the plan ends.</p></div>
    {error&&<p role="alert" className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <section className="rounded-xl border bg-white p-5"><div className="flex justify-between"><h2 className="font-semibold">Plans</h2><button className="rounded border px-3 py-1 text-sm" onClick={newPlan}>New plan</button></div>
      <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['Title','Type','Date range','Sections','Status','Priority','Created by','Published at','Affected regular periods','Actions'].map(x=><th key={x} className="whitespace-nowrap p-2">{x}</th>)}</tr></thead><tbody>{plans.map(p=><tr className="border-t" key={p.id}><td className="p-2">{p.title}</td><td className="p-2">{p.event_type}</td><td className="whitespace-nowrap p-2">{p.start_date} – {p.end_date}</td><td className="p-2">{p.sections?.length??0}</td><td className="p-2">{p.display_status??p.status}</td><td className="p-2">{p.priority}</td><td className="p-2">{p.created_by}</td><td className="p-2">{p.published_at??'—'}</td><td className="p-2">{p.affected_regular_periods??'—'}</td><td className="p-2"><button className="text-indigo-700 underline" onClick={()=>selectPlan(p)}>{p.status==='DRAFT'?'Edit':'View'}</button></td></tr>)}</tbody></table>{!plans.length&&<p className="p-3 text-sm text-slate-500">No plans yet.</p>}</div>
    </section>

    {(!plan||plan.status==='DRAFT')&&<><section className="grid gap-4 rounded-xl border bg-white p-5 md:grid-cols-2">
      <h2 className="md:col-span-2 font-semibold">1. Event details · 2. Date range</h2>
      <label className="text-sm">Title<input className="mt-1 block w-full rounded border p-2" value={title} onChange={e=>setTitle(e.target.value)}/>{!title.trim()&&<span className="text-xs text-red-700">Required</span>}</label>
      <label className="text-sm">Event type<select className="mt-1 block w-full rounded border p-2" value={type} onChange={e=>setType(e.target.value)}>{eventTypes.map(x=><option key={x}>{x}</option>)}</select></label>
      <label className="text-sm md:col-span-2">Notes<textarea className="mt-1 block w-full rounded border p-2" value={notes} onChange={e=>setNotes(e.target.value)}/></label>
      <label className="text-sm">Published version<select className="mt-1 block w-full rounded border p-2" value={version} disabled={!!plan} onChange={e=>setVersion(e.target.value)}><option value="">Choose version</option>{versions.map(v=><option key={v.id} value={v.id}>{v.title} · v{v.version_no}</option>)}</select></label>
      <label className="text-sm">Priority<input type="number" min="0" className="mt-1 block w-full rounded border p-2" value={priority} onChange={e=>setPriority(Number(e.target.value))}/></label>
      <label className="text-sm">Start date<input type="date" className="mt-1 block w-full rounded border p-2" value={start} onChange={e=>setStart(e.target.value)}/></label>
      <label className="text-sm">End date<input type="date" className="mt-1 block w-full rounded border p-2" value={end} onChange={e=>setEnd(e.target.value)}/>{start&&end&&start>end&&<span className="text-xs text-red-700">End date must be on or after start date.</span>}</label>

      <h2 className="md:col-span-2 font-semibold">3. Sections</h2>
      <label className="text-sm">Program<select className="mt-1 block w-full rounded border p-2" value={program} onChange={e=>setProgram(e.target.value)}><option value="">All programs</option>{programs.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label className="text-sm">Year<select className="mt-1 block w-full rounded border p-2" value={year} onChange={e=>setYear(e.target.value)}><option value="">All years</option>{[1,2,3,4,5].map(n=><option key={n}>{n}</option>)}</select></label>
      <div className="md:col-span-2"><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><input aria-label="Search sections" className="rounded border p-2 text-sm" placeholder="Search section, program, or year" value={sectionSearch} onChange={e=>setSectionSearch(e.target.value)}/><span className="text-sm text-slate-600">{filteredSections.length} sections available · {selectedSections.length} selected</span></div>
        <div className="mb-2 flex gap-4 text-sm"><button className="text-indigo-700 underline" onClick={()=>setSelectedSections(old=>allFilteredSelected?old.filter(id=>!filteredIds.includes(id)):Array.from(new Set([...old,...filteredIds])))}>{allFilteredSelected?'Deselect all filtered':'Select all filtered'}</button><span>{version?'Options limited to the selected published version’s semester.':'Showing all section master records.'}</span></div>
        <div className="max-h-72 space-y-3 overflow-auto rounded border p-3">{Object.entries(grouped).map(([group,items])=><fieldset key={group}><legend className="mb-1 text-sm font-medium text-slate-700">{group}</legend><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{items.map(s=><label key={s.id} className="text-sm"><input type="checkbox" checked={selectedSections.includes(String(s.id))} onChange={e=>setSelectedSections(old=>e.target.checked?Array.from(new Set([...old,String(s.id)])):old.filter(id=>id!==String(s.id)))}/> {s.name}</label>)}</div></fieldset>)}{!filteredSections.length&&<p className="text-sm text-slate-500">No sections match these filters.</p>}</div>
      </div>

      <div className="space-y-4 rounded-lg border p-4 md:col-span-2"><div><h2 className="font-semibold">4. Schedule Pattern</h2><p className="text-sm text-slate-500">Times come from the selected published timetable’s canonical slot template. Patterns are applied to every selected section.</p></div>
        {patterns.map((p,index)=><div key={p.id} className="space-y-3 rounded border p-3"><div className="flex items-center justify-between"><h3 className="font-medium">Pattern {index+1}</h3>{patterns.length>1&&<button className="text-sm text-red-700 underline" onClick={()=>setPatterns(old=>old.filter(item=>item.id!==p.id))}>Remove</button>}</div>
          <label className="block text-sm">Pattern type<select className="ml-2 rounded border p-2" value={p.mode} onChange={e=>updatePattern(p.id,{mode:e.target.value as 'weekday'|'date'})}><option value="weekday">Recurring days</option><option value="date">Specific date</option></select></label>
          {p.mode==='weekday'?<fieldset><legend className="text-sm">Days</legend><div className="flex flex-wrap gap-4">{weekdays.map((day,d)=><label className="text-sm" key={day}><input type="checkbox" checked={p.weekdays.includes(d)} onChange={e=>updatePattern(p.id,{weekdays:e.target.checked?[...p.weekdays,d]:p.weekdays.filter(x=>x!==d)})}/> {day}</label>)}</div>{!p.weekdays.length&&<p className="text-xs text-red-700">Select at least one weekday.</p>}</fieldset>:<label className="block text-sm">Specific date<input type="date" min={start} max={end} value={p.specificDate} onChange={e=>updatePattern(p.id,{specificDate:e.target.value})} className="ml-2 rounded border p-2"/></label>}
          <div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">Start time<select className="mt-1 block w-full rounded border p-2" value={p.startSlot} onChange={e=>updatePattern(p.id,{startSlot:e.target.value,endSlot:''})}><option value="">Choose start</option>{slots.filter(s=>!s.is_break).map(s=><option key={s.id} value={s.id}>{clockLabel(s.start_time)} · {s.label}</option>)}</select></label>
            <label className="text-sm">End time<select className="mt-1 block w-full rounded border p-2" value={p.endSlot} onChange={e=>updatePattern(p.id,{endSlot:e.target.value})}><option value="">Choose end</option>{slots.filter(s=>p.startSlot&&s.order>(slots.find(x=>String(x.id)===p.startSlot)?.order??0)).map(s=><option key={s.id} value={s.id}>{clockLabel(s.end_time)} · through {s.label}</option>)}</select></label></div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={p.override} onChange={e=>updatePattern(p.id,{override:e.target.checked})}/> Override regular classes during this pattern</label>
          {patternError(p)&&<p className="text-sm text-red-700">{patternError(p)}</p>}
        </div>)}
        <button className="text-sm text-indigo-700 underline" onClick={()=>setPatterns(old=>[...old,emptyPattern()])}>+ Add another time pattern</button>
      </div>

      <div className="grid gap-3 rounded-lg border p-4 md:col-span-2 md:grid-cols-2"><div className="md:col-span-2"><h2 className="font-semibold">5. Resources / Delivery</h2><p className="text-sm text-slate-500">Avoid assigning one physical room to simultaneous section sessions. Choose per-block assignment to fill rooms after creation.</p><p role={roomLoadError?'alert':undefined} className={roomLoadError?'text-sm text-red-700':'text-sm text-slate-500'}>{roomsLoading?'Loading rooms...':roomLoadError?'Unable to load rooms.':`${rooms.length} active rooms loaded.`}</p></div>
        <label className="text-sm">Room assignment<select className="mt-1 block w-full rounded border p-2" value={roomAssignment} onChange={e=>setRoomAssignment(e.target.value as 'NONE'|'SHARED'|'LATER')}><option value="NONE">No room / external training</option><option value="LATER">Assign rooms later / per block</option><option value="SHARED" disabled={selectedSections.length>1}>Same shared venue (one selected section only)</option></select></label>
        {roomAssignment==='SHARED'&&<label className="text-sm">Shared room<input aria-label="Search rooms" className="mt-1 block w-full rounded border p-2" placeholder="Search room, type, building, or floor" value={roomSearch} onChange={e=>setRoomSearch(e.target.value)}/><select className="mt-1 block w-full rounded border p-2" value={room} onChange={e=>setRoom(e.target.value)} disabled={roomsLoading||roomLoadError}><option value="">{roomsLoading?'Loading rooms...':roomLoadError?'Unable to load rooms.':'Choose room'}</option>{rooms.filter(r=>r.id===room||filteredRooms.some(option=>option.id===r.id)).map(r=><option key={r.id} value={r.id}>{r.code} — {r.building}{r.floor?` · Floor ${r.floor}`:''} — {r.room_type_label} — {r.capacity} seats</option>)}</select>{roomLoadError&&<span className="text-sm text-red-700">Unable to load rooms.</span>}</label>}
        <label className="text-sm">Delivery mode<select className="mt-1 block w-full rounded border p-2" value={delivery} onChange={e=>setDelivery(e.target.value)}><option value="OFFLINE">OFFLINE</option><option value="ONLINE">ONLINE</option></select></label>
        <label className="text-sm">Faculty trainer (optional)<select multiple className="mt-1 block h-24 w-full rounded border p-2" value={trainerIds} onChange={e=>setTrainerIds(Array.from(e.target.selectedOptions,x=>x.value))} disabled={facultyLoading}><option disabled>{facultyLoading?'Loading faculty...':''}</option>{faculty.map(f=><option key={f.id} value={f.id}>{f.name??f.employee_code}</option>)}</select><span className="text-xs text-slate-500">{faculty.length} active faculty options; selected faculty IDs are assigned to each section.</span></label>
        <label className="text-sm">External trainer name<input className="mt-1 block w-full rounded border p-2" value={trainerName} onChange={e=>setTrainerName(e.target.value)}/></label>
      </div>

      <div className="space-y-2 md:col-span-2"><h2 className="font-semibold">6. Review / Create Draft</h2><p className="text-sm">{title||'Untitled event'} · {start||'Start date'} – {end||'End date'} · {selectedSections.length} sections · {patterns.length} pattern(s)</p>{formErrors.length>0&&<ul className="list-inside list-disc text-sm text-red-700">{formErrors.map(message=><li key={message}>{message}</li>)}</ul>}
        <button className="rounded bg-indigo-700 px-4 py-2 text-white" disabled={busy||formErrors.length>0||(roomAssignment==='SHARED'&&selectedSections.length>1)|| (roomAssignment==='SHARED'&&!room)} onClick={save}>{plan?'Save draft':'Create Draft'}</button>
      </div>
    </section>

    {plan&&<section className="grid gap-4 rounded-xl border bg-white p-5 md:grid-cols-3"><h2 className="md:col-span-3 font-semibold">Draft pattern editor · Resources · Override</h2>
      <label className="text-sm">Pattern<select className="mt-1 block w-full rounded border p-2" value={patternMode} onChange={e=>setPatternMode(e.target.value as 'date'|'weekday')}><option value="weekday">Recurring weekday</option><option value="date">Specific date</option></select></label>
      {patternMode==='date'?<label className="text-sm">Specific date<input type="date" className="mt-1 block w-full rounded border p-2" value={specificDate} min={start} max={end} onChange={e=>setSpecificDate(e.target.value)}/></label>:<label className="text-sm">Weekday<select className="mt-1 block w-full rounded border p-2" value={weekday} onChange={e=>setWeekday(Number(e.target.value))}>{weekdays.map((x,i)=><option key={x} value={i}>{x}</option>)}</select></label>}
      <label className="text-sm">Start slot<select className="mt-1 block w-full rounded border p-2" value={slot} onChange={e=>setSlot(e.target.value)}><option value="">Choose slot</option>{slots.filter(x=>!x.is_break).map(x=><option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
      <label className="text-sm">Block length<input type="number" min="1" value={length} className="mt-1 block w-full rounded border p-2" onChange={e=>setLength(Number(e.target.value))}/></label>
      <label className="text-sm">Room<input aria-label="Search rooms" className="mt-1 block w-full rounded border p-2" placeholder="Search room, type, building, or floor" value={roomSearch} onChange={e=>setRoomSearch(e.target.value)}/><select className="mt-1 block w-full rounded border p-2" value={room} onChange={e=>setRoom(e.target.value)} disabled={roomsLoading||roomLoadError}><option value="">{roomsLoading?'Loading rooms...':roomLoadError?'Unable to load rooms.':'No room / assign later'}</option>{rooms.filter(x=>x.id===room||filteredRooms.some(option=>option.id===x.id)).map(x=><option key={x.id} value={x.id}>{x.code} — {x.building}{x.floor?` · Floor ${x.floor}`:''} — {x.room_type_label} — {x.capacity} seats</option>)}</select>{roomLoadError&&<span className="text-sm text-red-700">Unable to load rooms.</span>}</label>
      <label className="text-sm">Delivery<select className="mt-1 block w-full rounded border p-2" value={delivery} onChange={e=>setDelivery(e.target.value)}><option>OFFLINE</option><option>ONLINE</option></select></label>
      <label className="text-sm">Faculty / trainer<select multiple className="mt-1 block h-28 w-full rounded border p-2" value={trainerIds} onChange={e=>setTrainerIds(Array.from(e.target.selectedOptions,x=>x.value))} disabled={facultyLoading}>{facultyLoading&&<option disabled>Loading faculty...</option>}{faculty.map(x=><option key={x.id} value={x.id}>{x.name??x.employee_code}</option>)}</select></label>
      <label className="text-sm">External trainer name<input className="mt-1 block w-full rounded border p-2" value={trainerName} onChange={e=>setTrainerName(e.target.value)}/></label>
      <label className="flex items-center gap-2 text-sm md:col-span-3"><input type="checkbox" checked={override} onChange={e=>setOverride(e.target.checked)}/> Override overlapping regular classes for selected sections</label>
      <button className="rounded border px-4 py-2 md:col-span-3" disabled={busy||!slot||(patternMode==='date'&&!specificDate)} onClick={addPattern}>Add pattern to selected sections</button>
      <div className="md:col-span-3"><h3 className="font-medium">Patterns in draft</h3>{(plan.blocks??[]).map(b=><div className="flex justify-between border-t py-2 text-sm" key={b.id}><span>{sections.find(s=>String(s.id)===b.section)?.name} · {b.specific_date??weekdays[b.weekday]} · {slots.find(s=>String(s.id)===b.start_slot)?.label} · {b.block_length} periods · {b.event_type}</span><button className="text-red-700 underline" onClick={()=>removeBlock(b.id)}>Remove</button></div>)}</div>
    </section>}</>}

    {plan&&plan.status!=='DRAFT'&&<section className="rounded-xl border bg-white p-5"><h2 className="font-semibold">Published event patterns</h2>{(plan.blocks??[]).map(b=><p key={b.id} className="border-t py-2 text-sm">{sections.find(s=>String(s.id)===b.section)?.name} · {b.specific_date??weekdays[b.weekday]} · {slots.find(s=>String(s.id)===b.start_slot)?.label} · {b.block_length} periods · {b.title}</p>)}</section>}
    {plan&&<section className="space-y-3 rounded-xl border bg-white p-5"><h2 className="font-semibold">Preview / validate · Publish</h2><div className="flex flex-wrap gap-2"><button className="rounded border px-4 py-2" disabled={busy} onClick={()=>review('preview')}>Preview Impact</button><button className="rounded border px-4 py-2" disabled={busy} onClick={()=>review('validate')}>Validate</button>{plan.status==='DRAFT'&&<>{canPublish&&<button className="rounded bg-emerald-700 px-4 py-2 text-white" disabled={busy} onClick={()=>review('publish')}>Publish</button>}<button className="rounded border border-red-300 px-4 py-2 text-red-700" disabled={busy} onClick={deletePlan}>Delete draft</button></>}{plan.status==='PUBLISHED'&&canPublish&&<button className="rounded border border-red-300 px-4 py-2 text-red-700" disabled={busy} onClick={()=>review('cancel')}>Cancel published plan</button>}</div>
      {impact&&<><div className="grid gap-2 text-sm sm:grid-cols-3">{Object.entries(impact.summary??{}).map(([key,value])=><p className="rounded bg-slate-50 p-3" key={key}>{key.replaceAll('_',' ')}: <strong>{String(value)}</strong></p>)}</div>{impact.warnings?.length>0&&<div className="rounded bg-amber-50 p-3 text-sm text-amber-800">{impact.warnings.map((x,i)=><p key={i}>{x.date??''} · {x.message}</p>)}</div>}{impact.conflicts?.length>0&&<div role="alert" className="rounded bg-red-50 p-3 text-sm text-red-700">{impact.conflicts.map((x,i)=><p key={i}>{x.date??''} · {x.code} · {x.message}</p>)}</div>}<details><summary className="cursor-pointer text-sm">Temporary events and overridden classes</summary><pre className="max-h-80 overflow-auto p-3 text-xs">{JSON.stringify({temporary_events:impact.temporary_events,overridden_classes:impact.overridden_classes},null,2)}</pre></details></>}
    </section>}
  </main></AdminLayout>;
}
