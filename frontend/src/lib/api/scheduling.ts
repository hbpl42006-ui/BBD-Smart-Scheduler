import {apiClient} from './client';
export type Timetable={id:string;title:string;academic_session:string;semester:string;department:string;effective_date?:string;updated_at:string};
export type Version={id:string;version_no:number;status:string;entry_count:number;created_at:string;notes:string};
export const schedulingApi={list:async()=> (await apiClient.get<Timetable[]>('/timetables/')).data,create:async(payload:Record<string,unknown>)=>(await apiClient.post<Timetable>('/timetables/',payload)).data,versions:async(id:string)=>(await apiClient.get<Version[]>(`/timetables/${id}/versions/`)).data,entries:async(id:string)=>(await apiClient.get(`/versions/${id}/entries/`)).data,validate:async(id:string)=>(await apiClient.post(`/versions/${id}/validate/`)).data};
