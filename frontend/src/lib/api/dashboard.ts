import { apiClient } from './client';
export interface DashboardSummary { active_session: { id: string; name: string } | null; active_semester: { id: string; name: string } | null; counts: Record<string, number>; readiness: Record<string, boolean>; faculty_availability_status?: 'DEFAULT'|'CONFIGURED'|'INVALID'; faculty_availability_restrictions?: number; }
export async function getDashboardSummary() { const { data } = await apiClient.get<DashboardSummary>('/dashboard/summary/'); return data; }
