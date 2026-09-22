import {apiClient} from './client';
export type NotificationPreferences={in_app_enabled:boolean;email_enabled:boolean;whatsapp_enabled:boolean;whatsapp_opt_in:boolean;whatsapp_number:string;whatsapp_available:boolean};
export const notificationPreferencesApi={get:async()=> (await apiClient.get<NotificationPreferences>('/notifications/preferences/')).data,update:async(payload:Partial<NotificationPreferences>)=>(await apiClient.patch<NotificationPreferences>('/notifications/preferences/',payload)).data};
