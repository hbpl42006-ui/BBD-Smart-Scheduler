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
    return config;
  },
  async (error) => {
    return Promise.reject(error);
  }
);

apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  async (error) => {
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
