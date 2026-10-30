import React,{useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {CoeditLive} from '../../src/coedit-live';
import type {EditorAdapter} from '../../src/editor';
import '../../src/coedit.css';
function Fixture(){
 const adapter=useRef({bridge:{documentState:()=>({documentEpoch:1}),revision:()=>1,snapshot:()=>({evidence:{text:'viewer'}})},sdk:{getSelectionContext:async()=>({page:1}),pageCount:async()=>1,getPageSvg:async()=>'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1100"/>'}} as unknown as EditorAdapter);
 return <CoeditLive document={{id:'mock',name:'synthetic',current_version:'v1',created_at:1,role:'editor'}} adapter={adapter} token={useRef('synthetic-token')} version={useRef('v1')} epoch={useRef(1)} lastEdit={useRef(0)} dirty={useRef(false)} baseline={useRef(null)} enabled/>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
