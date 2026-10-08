'use client';

import { useState } from 'react';
import { ChevronDown, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { downloadExport } from '@/lib/api/export';

export function ExportButton({ endpoint, params = {} }: { endpoint: string; params?: Record<string, string | number | boolean | undefined> }) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const run = async (format: 'xlsx' | 'csv') => {
    setBusy(true); setOpen(false); setError('');
    try { await downloadExport(endpoint, format, params); }
    catch (cause) {
      const response = (cause as { response?: { data?: Blob | { detail?: string } } }).response;
      const body = response?.data;
      const detail = body instanceof Blob ? await body.text().catch(() => '') : body?.detail;
      setError(detail || 'Unable to export records.');
    } finally { setBusy(false); }
  };
  return <div className="relative">
    <Button type="button" variant="outline" disabled={busy} onClick={() => { setError(''); setOpen(value => !value); }}>
      <Download className="mr-2 h-4 w-4" />{busy ? 'Exporting...' : 'Export'}<ChevronDown className="ml-2 h-4 w-4" />
    </Button>
    {open && <div className="absolute right-0 z-30 mt-1 min-w-48 rounded-md border bg-white p-1 shadow-lg">
      <button type="button" className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-slate-100" onClick={() => void run('xlsx')}>Export Excel (.xlsx)</button>
      <button type="button" className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-slate-100" onClick={() => void run('csv')}>Export CSV (.csv)</button>
    </div>}
    {error && <p role="alert" className="absolute right-0 top-full z-20 mt-1 min-w-56 rounded bg-red-50 p-2 text-xs text-red-700">{error}</p>}
  </div>;
}
