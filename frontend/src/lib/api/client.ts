import axios from 'axios';

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    // We could attach tokens here if needed
    if (typeof window !== 'undefined') {
      const token = localStorage.getItem('access_token');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
    if (config.url?.includes('/generation-runs/')) console.log('[GEN CLIENT] sending generation POST', { url: `${config.baseURL ?? ''}${config.url}`, method: config.method, payload: config.data });
    return config;
  },
  async (error) => {
    return Promise.reject(error);
  }
);

apiClient.interceptors.response.use(
  (response) => {
    if (response.config.url?.includes('/generation-runs/')) console.log('[GEN CLIENT] generation response', { url: response.config.url, status: response.status, data: response.data });
    return response;
  },
  async (error) => {
    if (error.config?.url?.includes('/generation-runs/')) console.error('[GEN CLIENT] generation request failed', { url: error.config.url, status: error.response?.status, data: error.response?.data, message: error.message });
    if (error.response?.status === 401 && typeof window !== 'undefined' && !error.config?.url?.includes('/auth/')) {
      const refresh = localStorage.getItem('refresh_token');
      if (refresh && !error.config?.headers?.['X-Retry']) {
        try {
          const response = await axios.post(`${apiClient.defaults.baseURL}/auth/refresh/`, { refresh });
          localStorage.setItem('access_token', response.data.access);
          return apiClient({ ...error.config, headers: { ...error.config.headers, Authorization: `Bearer ${response.data.access}`, 'X-Retry': '1' } });
        } catch { localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token'); window.location.href = '/login'; }
      }
    }
    return Promise.reject(error);
  }
);
