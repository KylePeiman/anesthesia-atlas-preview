import {createContext,useContext} from 'react';
import type {State,Job,Issue,RecordResource} from './types';
export type ModalSpec={type:string;record?:any;resource?:string;proposal?:any;job?:any;previewJob?:Job};
export type AppContextType={state:State;manager:boolean;admin:boolean;busy:boolean;refresh:()=>Promise<void>;notify:(message:string,tone?:string)=>void;mutate:<T=any>(path:string,body?:unknown,method?:string)=>Promise<T>;open:(spec:ModalSpec)=>void;close:()=>void;selectedDay:string;setSelectedDay:(value:string)=>void;setView:(value:string)=>void;openChat:()=>void;selectedSite:string;setSelectedSite:(value:string)=>void;focusedRecord:{resource:string;id:string}|null;previewJob:Job|null;clearPreview:()=>void;scheduleData:State['data'];scheduleIssues:Issue[];navigateRecord:(resource:RecordResource,id:string,previewJob?:Job)=>void};
export const AppContext=createContext<AppContextType|null>(null);
export function useApp(){const c=useContext(AppContext);if(!c)throw new Error('App is not ready');return c;}
