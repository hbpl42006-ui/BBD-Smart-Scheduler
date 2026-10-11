'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { apiClient } from '@/lib/api/client';

type PreviewRow = {
  row: number; status: string; section?: string; course_code?: string; credit?: string;
  faculty_name?: string; employee_code?: string; day?: string; time_slot?: string;
  room?: string | null; delivery_mode?: 'ONLINE' | 'OFFLINE'; message?: string;
};
type Result = {
  total?: number; valid?: number; invalid?: number; detail?: string; error?: string;
  rows?: PreviewRow[]; errors?: { row: number; message: string; code?: string }[];
};

export function TimetableImportDialog({ versionId, onComplete }: { versionId: string; onComplete: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const close = () => { setOpen(false); setFile(null); setResult(null); setBusy(false); };
  const run = async (action: 'preview' | 'commit') => {
    if (!file || busy) return;
    const form = new FormData();
    form.append('file', file);
    setBusy(true);
    try {
      const { data } = await apiClient.post<Result>(`/versions/${versionId}/imports/timetable/${action}/`, form, { headers: { 'Content-Type': 'multipart/form-data' } });
      if (action === 'preview') setResult(data);
      else { close(); onComplete(); }
    } catch (error) {
      const response = (error as { response?: { data?: Result } }).response?.data;
      setResult(response ?? { invalid: 1, errors: [{ row: 0, message: 'Unable to import timetable.' }] });
    } finally { setBusy(false); }
  };
  const selectFile = (selected: File | null) => {
    const extension = selected?.name.toLowerCase().split('.').pop();
    setFile(selected && (extension === 'xlsx' || extension === 'csv') ? selected : null);
    setResult(null);
    setBusy(false);
  };
  const downloadTemplate = async () => {
    const { data } = await apiClient.get(`/versions/${versionId}/imports/timetable/template/`, { responseType: 'blob' });
    const url = URL.createObjectURL(data);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'timetable-import-template.xlsx';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return <>
    <Button variant="outline" onClick={() => setOpen(true)}>Import Timetable</Button>
    {open && <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-6xl rounded-xl bg-white p-6">
        <h2 className="text-lg font-semibold">Import Timetable</h2>
        <p className="mt-1 text-sm text-slate-500">Upload an Excel or CSV file to add schedule entries to this draft timetable version.</p>
        <Button variant="outline" className="mt-4" onClick={() => void downloadTemplate()}>Download Template</Button>
        <input className="my-4 block w-full" type="file" accept=".xlsx,.csv" onChange={event => selectFile(event.target.files?.[0] ?? null)} />
        <p className="text-sm text-slate-500">{file?.name ?? 'No file selected'}</p>
        {!file && <p className="mt-1 text-xs text-slate-500">Choose an Excel or CSV file.</p>}
        <div className="mt-4 flex gap-2">
          <Button disabled={!file || busy} onClick={() => void run('preview')}>{busy ? 'Previewing...' : 'Preview'}</Button>
          <Button disabled={!result || !!result.invalid || busy} onClick={() => void run('commit')}>Import</Button>
          <Button variant="outline" onClick={close}>Cancel</Button>
        </div>
        {result && <div className="mt-4 max-h-80 overflow-auto text-sm">
          <p className="mb-2">Total: {result.total ?? 0} · Valid: {result.valid ?? 0} · Invalid: {result.invalid ?? 0}</p>
          {result.rows && <table className="w-full min-w-[1150px] border-collapse text-left text-xs">
            <thead><tr>{['Status', 'Section', 'Course Code', 'Credit', 'Faculty Name', 'Employee Code', 'Day', 'Time Slot', 'Room', 'Delivery Mode', 'Issue'].map(label => <th className="border-b px-2 py-1 font-semibold" key={label}>{label}</th>)}</tr></thead>
            <tbody>{result.rows.map(row => <tr key={row.row} className={row.status === 'INVALID' ? 'text-red-700' : ''}>
              <td className="border-b px-2 py-1">{row.status}</td>
              <td className="border-b px-2 py-1">{row.section}</td>
              <td className="border-b px-2 py-1">{row.course_code}</td>
              <td className="border-b px-2 py-1">{row.credit}</td>
              <td className="border-b px-2 py-1">{row.faculty_name}</td>
              <td className="border-b px-2 py-1">{row.employee_code}</td>
              <td className="border-b px-2 py-1">{row.day}</td>
              <td className="border-b px-2 py-1">{row.time_slot}</td>
              <td className="border-b px-2 py-1">{row.room ?? '—'}</td>
              <td className="border-b px-2 py-1">{row.delivery_mode ?? '—'}</td>
              <td className="border-b px-2 py-1">{row.message}</td>
            </tr>)}</tbody>
          </table>}
          {(result.errors ?? []).map(error => <p className="text-red-600" key={`${error.row}-${error.message}`}>Row {error.row}: {error.message}</p>)}
        </div>}
      </div>
    </div>}
  </>;
}
