"""Static oracle for the 15-step edition's 18 documents; no runtime execution."""
import argparse,hashlib,json,re
from collections import Counter
from pathlib import Path
FILES=['README.md','resources/SOURCE_NOTES.md','output/README.md']+[f'docs/{n}.md' for n in ('architecture','supervisor_policy','hardware_adapter','validation_strategy','usage_walkthrough','build_record','reorganization')]+[f'knowledge/{n}.md' for n in ('parameter_dependencies','single_qubit_diagnostics','two_qubit_diagnostics','worked_cases','known_limitations','evidence_index','procedure_ownership','agent_internal_loop')]
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]);p.add_argument('--output',type=Path);a=p.parse_args();r=a.root.resolve();checks=[];texts={};hashes={}
def check(name,condition,detail=''):
 checks.append({'name':name,'passed':bool(condition),'detail':detail})
for name in FILES:
 path=r/name;t=path.read_text();texts[name]=t;hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest();check('nonempty:'+name,len(t)>600);check('fences:'+name,t.count('```')%2==0)
 for dest in re.findall(r'\[[^\]]*\]\(([^)]+)\)',t):
  if '://' in dest or dest.startswith('#'):continue
  check('link:'+name+':'+dest,(path.parent/dest.split('#')[0]).exists())
 check('no_stale_report_field:'+name,'grouped_report.json' not in t and 'full_completion' not in t)
 table_width=None
 for lineno,line in enumerate(t.splitlines(),1):
  if line.startswith('|'):
   width=len(re.findall(r'(?<!\\)\|',line))-1
   if table_width is None:table_width=width
   check(f'table:{name}:{lineno}',width==table_width,f'expected={table_width},actual={width}')
  else:table_width=None
ownership=json.loads((r/'config/workflow_ownership.json').read_text());owners=ownership['agents'];mapping={proc:x['id'] for x in owners for proc in x['procedure_ids']}
catalog=json.loads((r/'config/agent_catalog.json').read_text());by={x['id']:x for x in catalog['agents']}
check('15+1',len(owners)==15 and len(by)==16 and set(by)=={x['id'] for x in owners}|{'supervisor'})
check('8+7',sum(x['source_page']==1 for x in owners)==8 and sum(x['source_page']==2 for x in owners)==7)
check('44_unique_procedures',len(mapping)==sum(len(x['procedure_ids']) for x in owners)==44)
index=texts['knowledge/procedure_ownership.md'];readme=texts['README.md']
for x in owners:
 check('readme_owner:'+x['id'],x['id'] in readme and x['source_function'] in readme)
 check('catalog_owner:'+x['id'],by[x['id']]['procedure_ids']==x['procedure_ids'] and by[x['id']]['main_procedure_id']==x['main_procedure_id'])
 for proc in x['procedure_ids']:
  row=next((line for line in index.splitlines() if line.startswith('| `'+proc+'` |')), '')
  check('indexed_primary_owner:'+proc,bool(row) and x['id'] in row)
check('s04_s05',by['s04_readout_fidelity']['source_box_index']==4 and by['s05_readout_optimization']['source_box_index']==5)
check('d03_scan_main',by['d03_cz_width']['main_procedure_id']=='tq_cz_chevron')
for owner,main in [('s03_rabi_amplitude','sq_rabi_amplitude'),('s07_t2','sq_cpmg'),('d06_cz_xeb','tq_xeb')]:
 check('main_not_auxiliary:'+owner,by[owner]['main_procedure_id']==main and by[owner]['main_method_execution_status']=='contract_only')
# Preserve all previously explicit scientific inline formulas; whole method field
# preservation is independently checked by the catalog owner, not claimed here.
oldroot=r/'resources/previous_edition'
for name in ('parameter_dependencies','single_qubit_diagnostics','two_qubit_diagnostics','worked_cases','known_limitations'):
 old=(oldroot/f'knowledge/{name}.md').read_text();new=texts[f'knowledge/{name}.md']
 formulas=[v for v in re.findall(r'`([^`\n]+)`',old) if any(c in v for c in '=π√²≤')]
 for formula in formulas:check('preserved_formula:'+name+':'+formula,formula in new)
 if name=='worked_cases':
  oldnums=Counter(re.findall(r'\d+(?:\.\d+)?',old));newnums=Counter(re.findall(r'\d+(?:\.\d+)?',new));check('worked_case_numeric_examples_retained',all(newnums[k]>=v for k,v in oldnums.items()))
required={
 'docs/architecture.md':['caller_agent_id','primary_owner_id','procedure_runs','agent_execution_report.json','overall_workflow_complete','公共模块'],
 'docs/supervisor_policy.md':['s04','s05','d06','d07','DEVICE/UNKNOWN','STATISTICAL','DRIFT_OR_NONSTATIONARY','预算'],
 'docs/hardware_adapter.md':['不是32所','run-agent','软截止','reconcile','primary_owner_id'],
 'docs/validation_strategy.md':['Wilson','alpha spending','17','15实验与1总控','original_step_complete=false'],
 'docs/usage_walkthrough.md':['run-agent s03_rabi_amplitude','--procedure sq_rabi_time','s07_t2','d06_cz_xeb','source_suite_only'],
 'docs/reorganization.md':['8个','7个','QQswap','60 ns','s03','s07','d06'],
 'knowledge/agent_internal_loop.md':['采集','拟合','偏差','调参','重测','公共模块'],
 'knowledge/worked_cases.md':['合成示例','非设备标准','s02','s03','s04','s05','s07','s08','d02','d03','d04','d05','d06','d07'],
 'output/README.md':['previous_edition','tests_run','原流程15步版_20261024_验收.json']}
for name,tokens in required.items():
 for token in tokens:check('coverage:'+name+':'+token,token in texts[name])
check('old_QA_archived',(oldroot/'reorganization_archive/knowledge_qa/document_freeze_manifest.json').is_file())
check('active_no_old_QA_pass',not (r/'knowledge/qa/acceptance_first.json').exists())
result={'schema_version':2,'edition':'original_workflow_15_steps','scope':'18份活跃文档的静态结构、链接、归属、科学公式保留和边界核对；非运行/浏览器验收','passed':all(x['passed'] for x in checks),'checks':checks,'characters':sum(map(len,texts.values())),'artifact_sha256':hashes}
if a.output:a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':result['passed'],'checks':len(checks),'documents':len(FILES),'characters':result['characters'],'failed':[x for x in checks if not x['passed']]},ensure_ascii=False));raise SystemExit(0 if result['passed'] else 1)
