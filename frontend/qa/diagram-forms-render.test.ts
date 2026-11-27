import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {generatedDiagramSource,readableDiagramSource} from './fixtures/generated-diagram-source.ts';
function render(name:string,props:Record<string,unknown>){const compiled=buildSync({entryPoints:[fileURLToPath(new URL(`../src/components/${name}.tsx`,import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;const module={exports:{} as {default:Parameters<typeof createElement>[0]}};new Function('require','module','exports',compiled)(createRequire(import.meta.url),module,module.exports);return renderToStaticMarkup(createElement(module.exports.default,props));}
test('topology reference uses original pixel coordinates and preserves unmapped source image',()=>{
 const image={name:'design.png',dataUrl:'data:image/png;base64,AA==',width:1000,height:500};const html=render('ChipTopology',{scope:'a',topology:{image,nodes:[{resourceId:'Q1',designId:'D7',x:250,y:120}],edges:[]}});assert.match(html,/<image[^>]*href="data:image\/png;base64,AA=="/);assert.match(html,/<circle[^>]*cx="250"[^>]*cy="120"/);assert.match(html,/D7/);assert.match(render('ChipTopology',{scope:'a',topology:{image,nodes:[],edges:[]}}),/<image/);
});

test('DOCX topology preview hides generated identifiers in closed native evidence details',()=>{
 const image={name:'image2.png',dataUrl:'data:image/png;base64,AA==',width:1000,height:500,source:generatedDiagramSource};
 const topology={image,source:generatedDiagramSource,nodes:[],edges:[]};
 const html=render('ChipTopology',{scope:'imported',topology});
 const visible=html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g,'');
 assert.ok(visible.includes(readableDiagramSource));
 assert.doesNotMatch(visible,/word\/document\.xml|word\/media\/|import-012345|SHA-256/);
 assert.match(html,/<details[^>]*><summary>完整来源证据<\/summary>/);
 assert.ok(html.includes(generatedDiagramSource));assert.equal(topology.source,generatedDiagramSource);assert.equal(topology.image.source,generatedDiagramSource);
 const fallback=render('ChipTopology',{scope:'image-only',topology:{image,nodes:[],edges:[]}});
 assert.ok(fallback.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g,'').includes(readableDiagramSource));
});
