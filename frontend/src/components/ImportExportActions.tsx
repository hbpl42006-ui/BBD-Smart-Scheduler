'use client';

import { BulkImportDialog } from '@/components/BulkImportDialog';
import { ExportButton } from '@/components/ExportButton';

type ImportExportActionsProps = {
  kind: string;
  endpoint?: string;
  canImport: boolean;
  onComplete: () => void;
  params?: Record<string, string | number | boolean | undefined>;
};

export function ImportExportActions({ kind, endpoint, canImport, onComplete, params }: ImportExportActionsProps) {
  return <div className="flex flex-wrap items-center gap-2">
    {canImport && <BulkImportDialog kind={kind} onComplete={onComplete} />}
    {endpoint && <ExportButton endpoint={endpoint} params={params} />}
  </div>;
}
