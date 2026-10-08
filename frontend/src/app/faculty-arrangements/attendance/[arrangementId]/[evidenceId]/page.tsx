'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { fetchArrangementEvidence } from '@/lib/api/arrangements';

export default function ArrangementAttendanceEvidencePage() {
  const params = useParams<{ arrangementId: string; evidenceId: string }>();
  const [fileUrl, setFileUrl] = useState('');
  const [mimeType, setMimeType] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    let objectUrl = '';
    fetchArrangementEvidence(params.arrangementId, params.evidenceId)
      .then(blob => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setFileUrl(objectUrl);
        setMimeType(blob.type);
      })
      .catch(() => { if (active) setError('This attendance file is unavailable or you do not have permission to view it.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [params.arrangementId, params.evidenceId]);

  return <AdminLayout><Header title="Arrangement Attendance" /><main className="space-y-4 p-5 sm:p-8">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">Attendance Evidence</h1><div className="flex gap-2"><Link href="/dashboard"><Button variant="outline">Back to Dashboard</Button></Link>{fileUrl && <a href={fileUrl} download><Button>Download</Button></a>}</div></div>
    <Card><CardHeader><CardTitle>Uploaded attendance</CardTitle></CardHeader><CardContent>
      {loading ? <p>Loading attendance evidence...</p> : error ? <p role="alert" className="text-sm text-red-700">{error}</p> : mimeType.startsWith('image/')
        ? <Image src={fileUrl} alt="Uploaded arrangement attendance evidence" width={1200} height={900} unoptimized className="mx-auto max-h-[75vh] max-w-full object-contain" />
        : <iframe src={fileUrl} title="Uploaded attendance evidence" className="h-[75vh] w-full rounded border" />}
    </CardContent></Card>
  </main></AdminLayout>;
}
