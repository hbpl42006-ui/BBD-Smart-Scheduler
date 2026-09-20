import React from 'react';
import { AppSidebar } from './Sidebar';
import { SidebarProvider } from '@/components/ui/sidebar';

export function AdminLayout({ children }: { children: React.ReactNode }) {
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
