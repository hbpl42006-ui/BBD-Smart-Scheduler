import { apiClient } from './client';

export type ReceivedArrangementAttendance = {
  id: string;
  arrangement_id: string;
  date: string;
  time_slot: { label: string; start_time: string; end_time: string };
  course: { code: string; name: string };
  section: { id: string; name: string };
  original_faculty: { id: string; name: string };
  arrangement_faculty: { id: string; name: string };
  attendance_status: 'UPLOADED' | 'PENDING';
  evidence: { id: string; original_filename: string; mime_type: string; file_size: number; uploaded_at: string; view_url: string };
};

export async function getReceivedArrangementAttendance() {
  const { data } = await apiClient.get<{ count: number; results: ReceivedArrangementAttendance[] }>('/faculty-arrangements/received-attendance/');
  return data;
}

export async function fetchArrangementEvidence(arrangementId: string, evidenceId: string) {
  const { data } = await apiClient.get<Blob>(`/faculty-arrangements/${arrangementId}/attendance/${evidenceId}/view/`, { responseType: 'blob' });
  return data;
}
