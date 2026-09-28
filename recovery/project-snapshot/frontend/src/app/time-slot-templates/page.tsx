import { CrudPage } from '@/components/CrudPage';
export default function Page(){return <CrudPage title="Time Slot Templates" description="Manage institution-specific time slot templates." endpoint="time-slot-templates" fields={[{key:'name',label:'Template name',required:true},{key:'institution',label:'Institution',required:true,relation:{endpoint:'institutions',label:'name'}}]}/>}
