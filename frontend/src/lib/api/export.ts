import { apiClient } from './client';

export async function downloadExport(endpoint: string, format: 'xlsx' | 'csv', params: Record<string, string | number | boolean | undefined> = {}) {
  const { data, headers } = await apiClient.get<Blob>(`/${endpoint}/export/`, { params: { ...params, format }, responseType: 'blob' });
  const disposition = String(headers['content-disposition'] ?? '');
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const filename = encodedName ? decodeURIComponent(encodedName) : plainName || `${endpoint}.${format}`;
  const objectUrl = URL.createObjectURL(data);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return filename;
}
