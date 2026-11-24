// Shared by the workspace allowlist and narrow public-text normalization.
// These tools only read scoped controller/document/source records.
export const inspectionDefinitions=[
  {name:'list_source_files',inputSchema:{type:'object',properties:{pathPrefix:{type:'string'},limit:{type:'number'}},additionalProperties:false}},
  {name:'search_source',description:'Case-sensitive literal substring search of allowlisted project source only, not semantic search or uploaded documents. Prefer a short source identifier or keyword; no match does not prove absence.',inputSchema:{type:'object',required:['query'],properties:{query:{type:'string',description:'One short literal keyword or exact source identifier. Whitespace is matched literally; multiple words are not OR/AND terms.'},pathPrefix:{type:'string'},limit:{type:'number'}},additionalProperties:false}},
  {name:'read_source',inputSchema:{type:'object',required:['path'],properties:{path:{type:'string'},startLine:{type:'number'},endLine:{type:'number'}},additionalProperties:false}},
  {name:'inspect_runtime',inputSchema:{type:'object',required:['kind'],properties:{kind:{type:'string',enum:['health','catalog','resources','workflows','attention']}},additionalProperties:false}},
  {name:'inspect_workflow',inputSchema:{type:'object',required:['workflowId'],properties:{workflowId:{type:'string'},nodeId:{type:'string'}},additionalProperties:false}},
  {name:'inspect_configuration',inputSchema:{type:'object',required:['kind','id'],properties:{kind:{type:'string',enum:['chip','deployment']},id:{type:'string'}},additionalProperties:false}},
  {name:'inspect_report',inputSchema:{type:'object',required:['workflowId'],properties:{workflowId:{type:'string'}},additionalProperties:false}},
  {name:'inspect_report_export',inputSchema:{type:'object',required:['exportId'],properties:{exportId:{type:'string'}},additionalProperties:false}},
];
export const workspaceReadDefinitions=[...inspectionDefinitions,
  {name:'list_workspace',inputSchema:{type:'object',properties:{},additionalProperties:false}},
  {name:'list_documents',inputSchema:{type:'object',properties:{query:{type:'string'},offset:{type:'number'},limit:{type:'number'}},additionalProperties:false}},
  {name:'read_document',inputSchema:{type:'object',required:['documentId'],properties:{documentId:{type:'string'},query:{type:'string'},offset:{type:'number'},limit:{type:'number'}},additionalProperties:false}},
];
