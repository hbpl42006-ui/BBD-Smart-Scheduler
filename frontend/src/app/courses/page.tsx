import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Courses" description="Manage course catalog." endpoint="courses" fields={[{key:'code',label:'Code',required:true},{key:'name',label:'Name',required:true},{key:'short_code',label:'Short code',required:true},{key:'credit',label:'Credit',type:'number',required:true}]}/>}

