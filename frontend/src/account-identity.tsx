import {createContext, useContext, useEffect, useState, type ReactNode} from 'react';
import type {Account} from './account-gate';
import type {Affiliation} from './organization';
import {api,errorMessage} from './api';

export const AccountIdentity = createContext<Account | null>(null);
export const useAccountIdentity = () => useContext(AccountIdentity);
const OrganizationIdentity=createContext<{value:Affiliation|null;error:string;reload:()=>void}|null>(null);
export const useOrganizationIdentity=()=>useContext(OrganizationIdentity);
export function AccountIdentityProvider({account,children}:{account:Account;children:ReactNode}){
 const [value,setValue]=useState<Affiliation|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 useEffect(()=>{setValue(null);setError('');let controller:AbortController|undefined;let active=true;
  const refresh=()=>{controller?.abort();const current=controller=new AbortController();void api<Affiliation>('/organization/me',{signal:current.signal}).then(result=>{if(active&&!current.signal.aborted){setValue(result);setError('');}}).catch(cause=>{if(active&&!current.signal.aborted){setValue(null);setError(errorMessage(cause));}});};
  const visible=()=>{if(document.visibilityState==='visible')refresh();};
  refresh();window.addEventListener('focus',refresh);window.addEventListener('hwp-organization-updated',refresh);document.addEventListener('visibilitychange',visible);
  return()=>{active=false;controller?.abort();window.removeEventListener('focus',refresh);window.removeEventListener('hwp-organization-updated',refresh);document.removeEventListener('visibilitychange',visible);};
 },[account.user_id,revision]);
 return <AccountIdentity.Provider value={account}><OrganizationIdentity.Provider value={{value,error,reload:()=>setRevision(n=>n+1)}}>{children}</OrganizationIdentity.Provider></AccountIdentity.Provider>;
}
