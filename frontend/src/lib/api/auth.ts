import { apiClient } from './client';
import type { User } from './types';
export async function login(email: string, password: string) { const { data } = await apiClient.post('/auth/login/', { email, password }); if (typeof window !== 'undefined') { localStorage.setItem('access_token', data.access); localStorage.setItem('refresh_token', data.refresh); } return data.user as User; }
export async function me() { const { data } = await apiClient.get<User>('/auth/me/'); return data; }
export function logout() { if (typeof window !== 'undefined') { localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token'); window.location.href = '/login'; } }
