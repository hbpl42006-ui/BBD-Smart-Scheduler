import { apiClient } from './client';
import type { NotificationItem } from './types';
export async function listNotifications(unread=false){const {data}=await apiClient.get<{results:NotificationItem[];count:number}>('/notifications/',{params:unread?{unread:'true'}:undefined});return data;}
export async function unreadNotificationCount(){return (await apiClient.get<{count:number}>('/notifications/unread-count/')).data.count;}
export async function markNotificationRead(id:string){return (await apiClient.post<NotificationItem>(`/notifications/${id}/read/`)).data;}
export async function markAllNotificationsRead(){await apiClient.post('/notifications/mark-all-read/');}
export const NOTIFICATION_UNREAD_CHANGED='bbd:notification-unread-changed';
export function publishUnreadNotificationCount(count:number){if(typeof window!=='undefined')window.dispatchEvent(new CustomEvent(NOTIFICATION_UNREAD_CHANGED,{detail:{count}}));}
