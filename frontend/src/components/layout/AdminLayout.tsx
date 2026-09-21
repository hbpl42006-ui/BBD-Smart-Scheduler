'use client';
import React from 'react';
import { AppSidebar } from './Sidebar';
import { SidebarProvider } from '@/components/ui/sidebar';
import {useEffect,useState} from 'react';
import {useRouter} from 'next/navigation';
import {me} from '@/lib/api/auth';

export function AdminLayout({ children }: { children: React.ReactNode }) {
  const router=useRouter(); const [ready,setReady]=useState(false);
  useEffect(()=>{me().then(()=>setReady(true)).catch(()=>router.replace('/login'))},[router]);
  if(!ready)return <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">Loading session…</div>;
  return (
    <SidebarProvider>
      <div className="flex h-screen overflow-hidden bg-zinc-50 w-full">
        <AppSidebar />
        <main className="flex-1 flex flex-col h-screen overflow-y-auto w-full">
          {children}
        </main>
      </div>
    </SidebarProvider>
  );
}
