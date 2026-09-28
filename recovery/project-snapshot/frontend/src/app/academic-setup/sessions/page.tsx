import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Academic Sessions" description="Set active academic sessions and dates." endpoint="academic-sessions" fields={[{key:'name',label:'Name',required:true},{key:'institution',label:'Institution ID',required:true},{key:'start_date',label:'Start date',type:'date',required:true},{key:'end_date',label:'End date',type:'date',required:true},{key:'is_active',label:'Active'}]}/>}

