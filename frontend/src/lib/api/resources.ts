import { apiClient } from './client';
export type Entity = Record<string, unknown> & { id: string };
export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };
export async function list<T extends Entity>(endpoint: string, params: Record<string, string | number | undefined> = {}) { const { data } = await apiClient.get<Page<T>>(`/${endpoint}/`, { params }); return data; }
export async function create<T extends Entity>(endpoint: string, payload: Record<string, unknown>) { const { data } = await apiClient.post<T>(`/${endpoint}/`, payload); return data; }
export async function update<T extends Entity>(endpoint: string, id: string, payload: Record<string, unknown>) { const { data } = await apiClient.patch<T>(`/${endpoint}/${id}/`, payload); return data; }
export async function deactivate(endpoint: string, id: string) { await apiClient.delete(`/${endpoint}/${id}/`); }
export async function uploadImport(kind: string, action: 'preview'|'commit', file: File) { const form = new FormData(); form.append('file', file); const { data } = await apiClient.post(`/imports/${kind}/${action}/`, form, { headers: { 'Content-Type': 'multipart/form-data' } }); return data; }
export async function downloadRoomImportTemplate() { const { data } = await apiClient.get('/imports/rooms/template/', { responseType: 'blob' }); const url = URL.createObjectURL(data); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'rooms-import-template.xlsx'; anchor.click(); URL.revokeObjectURL(url); }
export async function downloadFacultyAvailabilityTemplate() { const { data } = await apiClient.get('/imports/faculty-availability/template/', { responseType: 'blob' }); const url = URL.createObjectURL(data); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'faculty-availability-template.xlsx'; anchor.click(); URL.revokeObjectURL(url); }
