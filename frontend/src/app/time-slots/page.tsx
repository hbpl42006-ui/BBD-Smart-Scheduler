import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Time Slots" description="Define configured teaching periods." endpoint="time-slots" fields={[{key:'template',label:'Time Slot Template',required:true,relation:{endpoint:'time-slot-templates',label:'name'}},{key:'label',label:'Label',required:true},{key:'start_time',label:'Start time',type:'time',required:true},{key:'end_time',label:'End time',type:'time',required:true},{key:'order',label:'Order',type:'number',required:true},{key:'is_break',label:'Break'}]}/>}

