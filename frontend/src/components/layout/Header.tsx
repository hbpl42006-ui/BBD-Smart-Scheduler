'use client';
import React, { useEffect, useState } from 'react';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { Bell, ChevronDown } from 'lucide-react';
import { me, logout } from '@/lib/api/auth';
import type { User } from '@/lib/api/types';

export function Header({ title }: { title: string }) {
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => { me().then(setUser).catch(() => undefined); }, []);
  const initials = user ? `${user.first_name?.[0] ?? ''}${user.last_name?.[0] ?? ''}` : '...';
  return (
    <header className="no-print sticky top-0 z-10 flex h-16 items-center gap-4 border-b bg-white/95 px-4 sm:px-8">
      <SidebarTrigger />
      <div className="flex-1"><p className="text-xs text-slate-400">Academic Management /</p><h1 className="text-base font-semibold text-slate-900">{title}</h1></div>
      <button className="relative rounded-md p-2 text-slate-500 hover:bg-slate-100" aria-label="Notifications"><Bell className="h-5 w-5" /><span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-blue-600" /></button>
      <button onClick={logout} title="Sign out" className="hidden items-center gap-2 border-l pl-4 text-left sm:flex"><div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700">{initials}</div><div><p className="text-sm font-medium">{user ? `${user.first_name} ${user.last_name}` : 'Loading...'}</p><p className="text-xs text-slate-400">{user?.role ?? user?.email ?? ''}</p></div><ChevronDown className="h-4 w-4 text-slate-400" /></button>
    </header>
  );
}
