import { Plus } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { ExportButton } from '@/components/ExportButton';
const exportEndpoints: Record<string, string> = { Faculty: 'faculty', Sections: 'sections', 'Rooms & Labs': 'rooms', Courses: 'courses', 'Course Offerings': 'course-offerings' };
export function PageHeader({ title, description, action, extraAction }: { title: string; description: string; action?: string; extraAction?: ReactNode }) { const endpoint = exportEndpoints[title]; return <div className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><h2 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h2><p className="mt-1 text-sm text-slate-500">{description}</p></div><div className="flex items-center gap-2">{extraAction}{endpoint && !['Sections', 'Course Offerings', 'Courses'].includes(title) && <ExportButton endpoint={endpoint} />}{action && <Button className="gap-2 bg-blue-700 hover:bg-blue-800"><Plus className="h-4 w-4" />{action}</Button>}</div></div>; }
