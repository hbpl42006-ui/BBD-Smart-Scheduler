import { apiClient } from './client';
export interface DashboardSummary { active_session: { id: string; name: string } | null; active_semester: { id: string; name: string } | null; counts: Record<string, number>; readiness: Record<string, boolean>; }
export async function getDashboardSummary() { const { data } = await apiClient.get<DashboardSummary>('/dashboard/summary/'); return data; }
