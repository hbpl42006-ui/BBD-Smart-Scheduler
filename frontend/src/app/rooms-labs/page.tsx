import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Rooms & Labs" description="Manage teaching spaces." endpoint="rooms" fields={[{key:'code',label:'Code',required:true},{key:'building',label:'Building',required:true},{key:'floor',label:'Floor',required:true},{key:'capacity',label:'Capacity',type:'number',required:true},{key:'room_type',label:'Room type',required:true}]}/>}

