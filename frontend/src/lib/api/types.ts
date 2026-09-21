export interface ApiResponse<T = unknown> {
  data: T;
  message?: string;
  success: boolean;
}
export interface User { id: string; email: string; first_name: string; last_name: string; role: string; is_active: boolean; is_staff: boolean; faculty_id?: string | null; }

export interface PaginatedResponse<T = unknown> {
  data: T[];
  total: number;
  page: number;
  limit: number;
  success: boolean;
}
