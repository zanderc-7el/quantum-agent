import {importedDiagramSourceLabel} from '../lib/diagramSource';

export function DiagramSourceEvidence({source,label='完整来源证据'}:{source?:string;label?:string}) {
 if(!importedDiagramSourceLabel(source))return null;
 return <details><summary>{label}</summary><p className="cd-reference-caption">{source}</p></details>;
}

export function DiagramSourceField({label,value,onChange,placeholder}:{label:string;value?:string;onChange(value:string):void;placeholder?:string}) {
 const summary=importedDiagramSourceLabel(value);
 // An imported evidence string is immutable in the ordinary form. Showing its
 // summary must not turn the next keystroke into replacement of the evidence.
 return <div><label>{label}{summary
  ? <input aria-label={label} value={summary} readOnly/>
  : <input aria-label={label} value={value??''} placeholder={placeholder} onChange={e=>onChange(e.target.value)}/>}</label>
  <DiagramSourceEvidence source={value}/></div>;
}
