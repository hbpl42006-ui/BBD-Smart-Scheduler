import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Sections" description="Manage student sections." endpoint="sections" fields={[{key:'program',label:'Program',required:true,relation:{endpoint:'programs',label:'name'}},{key:'semester',label:'Semester',required:true,relation:{endpoint:'semesters',label:'name'}},{key:'year',label:'Year',type:'number',required:true},{key:'name',label:'Name',required:true},{key:'student_strength',label:'Student strength',type:'number',required:true}]}/>}

