import { apiClient } from './client';
import type { LoginRequest,LoginResponse,AuthenticatedUser } from './types';
export async function login(email: string, password: string) { const body:LoginRequest={email,password}; const { data } = await apiClient.post<LoginResponse>('/auth/login/', body); if (typeof window !== 'undefined') { localStorage.setItem('access_token', data.access); localStorage.setItem('refresh_token', data.refresh); } return data.user; }
export async function me() { const { data } = await apiClient.get<AuthenticatedUser>('/auth/me/'); return data; }
export function logout() { if (typeof window !== 'undefined') { localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token'); window.location.href = '/login'; } }
