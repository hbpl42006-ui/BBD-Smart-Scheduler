import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Semesters" description="Configure semester periods." endpoint="semesters" fields={[{key:'session',label:'Academic Session',required:true,relation:{endpoint:'academic-sessions',label:'name'}},{key:'name',label:'Name',required:true},{key:'number',label:'Number',type:'number',required:true},{key:'type',label:'ODD or EVEN',required:true},{key:'start_date',label:'Start date',type:'date',required:true},{key:'end_date',label:'End date',type:'date',required:true}]}/>}

