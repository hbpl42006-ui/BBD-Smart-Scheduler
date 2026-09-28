import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Programs" description="Manage academic programs." endpoint="programs" fields={[{key:'department',label:'Department',required:true,relation:{endpoint:'departments',label:'name'}},{key:'name',label:'Name',required:true},{key:'code',label:'Code',required:true},{key:'duration_years',label:'Duration years',type:'number',required:true}]}/>}

