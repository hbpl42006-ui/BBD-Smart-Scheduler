export interface ApiResponse<T = unknown> {
  data: T;
  message?: string;
  success: boolean;
}
export interface AuthenticatedUser { id: string; email: string; first_name: string; last_name: string; role: string; is_active: boolean; is_staff: boolean; faculty_id?: string | null; }
export type User = AuthenticatedUser;
export interface LoginRequest { email:string; password:string; }
export interface LoginResponse { access:string; refresh:string; user:AuthenticatedUser; }

export interface PaginatedResponse<T = unknown> {
  data: T[];
  total: number;
  page: number;
  limit: number;
  success: boolean;
}
