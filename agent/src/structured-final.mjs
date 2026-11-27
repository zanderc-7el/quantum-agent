// Compatibility for the observed nested final JSON's literal message controls.
// This never extracts a prefix or repairs a proposal, tool argument, or quote.
import {createHash} from 'node:crypto';

export function parseWorkspaceContent(text){
 try{return {value:JSON.parse(text)};}catch(original){
  if(typeof text!=='string'||text.length>200000)throw original;
  let output='',inside=false,escaped=false,message=false,depth=0,count=0;
  for(let index=0;index<text.length;index++){
   const char=text[index];
   if(inside){
    if(escaped){output+=char;escaped=false;continue;}
    if(char==='\\'){output+=char;escaped=true;continue;}
    if(char==='"'){inside=false;output+=char;continue;}
    if(char.charCodeAt(0)<32){
     const replacement=char==='\n'?'\\n':char==='\r'?'\\r':char==='\t'?'\\t':null;
     if(!message||!replacement)throw original;
     output+=replacement;count++;continue;
    }
   }else if(char==='"'){
    inside=true;
    // Only a literal top-level message key qualifies. Other JSON strings stay
    // byte-for-byte unchanged and must satisfy the original JSON grammar.
    message=depth===1&&/"message"\s*:\s*$/.test(text.slice(0,index));
   }else if(char==='{'||char==='[')depth++;
   else if(char==='}'||char===']')depth--;
   output+=char;
  }
  if(!count)throw original;
  // Full-document parsing still rejects trailing actions, malformed quotes,
  // invalid escapes, extra documents and controls anywhere else in the JSON.
  const value=JSON.parse(output);
  return {value,normalization:{formatVersion:'workspace-message-literal-controls/1',field:'message',
   originalContentSha256:createHash('sha256').update(text).digest('hex'),escapedControlCharacters:count}};
 }
}
