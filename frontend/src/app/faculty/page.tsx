import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Faculty" description="Manage faculty profiles." endpoint="faculty" fields={[{key:'user',label:'User ID',required:true},{key:'employee_code',label:'Employee code',required:true},{key:'initials',label:'Initials',required:true},{key:'department',label:'Department',required:true,relation:{endpoint:'departments',label:'name'}},{key:'max_daily_periods',label:'Max daily periods',type:'number',required:true},{key:'max_weekly_periods',label:'Max weekly periods',type:'number',required:true}]}/>}

