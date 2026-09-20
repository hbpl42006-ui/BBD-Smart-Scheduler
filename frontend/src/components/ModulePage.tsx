import { ClipboardList } from 'lucide-react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';
import { PageHeader } from '@/components/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
export function ModulePage({ title, description, action }: { title: string; description: string; action?: string }) { return <AdminLayout><Header title={title} /><main className="mx-auto w-full max-w-[1500px] p-5 sm:p-8"><PageHeader title={title} description={description} action={action} /><Card className="border-slate-200 shadow-sm"><CardContent className="flex min-h-[300px] flex-col items-center justify-center p-8 text-center"><div className="mb-4 rounded-full bg-blue-50 p-4 text-blue-700"><ClipboardList className="h-7 w-7" /></div><h3 className="font-semibold text-slate-900">No records configured yet</h3><p className="mt-2 max-w-md text-sm text-slate-500">Connect the academic administration service to manage {title.toLowerCase()} here. This workspace will show live records once the module is available.</p></CardContent></Card></main></AdminLayout>; }
