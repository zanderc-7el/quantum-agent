import test from 'node:test';
import assert from 'node:assert/strict';
import {readExternalScienceJSON} from '../src/lib/scienceExternalFile.ts';

test('external JSON imports preserve original UTF-8 BOM and every byte for backend provenance',async()=>{
 const bytes=new TextEncoder().encode('\uFEFF{\n "source":"外部文件", "number":1.2300\n}\n');
 const input=await readExternalScienceJSON(new File([bytes],'external.JSON'),1024);
 assert.equal(input.fileName,'external.JSON');assert.deepEqual(Buffer.from(input.documentBase64,'base64'),Buffer.from(bytes));
});
test('invalid or oversized external files never become analysis input',async()=>{
 for(const file of [new File(['{}'],'wrong.csv'),new File([new Uint8Array([0xff])],'bad.json'),new File(['{'],'partial.json'),new File(['[]'],'array.json'),new File(['null'],'null.json'),new File([],'empty.json'),new File(['x'.repeat(1025)],'large.json')])await assert.rejects(readExternalScienceJSON(file,1024));
 await assert.rejects(readExternalScienceJSON({name:'wrong-size.json',size:2,arrayBuffer:async()=>new Uint8Array(1025).buffer} as File,1024));
});
