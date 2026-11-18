"""Portable CLI. No command connects real hardware or starts a background service."""
from __future__ import annotations
import argparse,hashlib,json,sys
from contextlib import redirect_stdout
from pathlib import Path

def emit(value):print(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)

def main(argv=None):
    parser=argparse.ArgumentParser(description='原流程15个智能体+1总控；物理procedure是内部程序，不是额外agent')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list',help='列出15个原流程角色和1个总控及局部绑定状态')
    show=sub.add_parser('show');show.add_argument('agent_id')
    plan=sub.add_parser('plan');plan.add_argument('--domain',default='all');plan.add_argument('--changed',nargs='*')
    demo=sub.add_parser('demo');demo.add_argument('--output',required=True)
    twin=sub.add_parser('twin');twin.add_argument('--output',required=True);twin.add_argument('--qubits',default='0,1');twin.add_argument('--shots',type=int,default=2048);twin.add_argument('--seed',type=int,default=20261024);twin.add_argument('--operation-id',default='twin-workflow')
    cz=sub.add_parser('cz');cz.add_argument('--output',required=True);cz.add_argument('--seed',type=int,default=20261024)
    status=sub.add_parser('status');status.add_argument('--db',required=True)
    resume=sub.add_parser('resume');resume.add_argument('--db',required=True);resume.add_argument('--run-id',required=True);resume.add_argument('--backend-dir',required=True);resume.add_argument('--seed',type=int,default=20261024)
    propose=sub.add_parser('propose');propose.add_argument('agent_id');propose.add_argument('--context',required=True);propose.add_argument('--provider',choices=['rules','openai'],default='rules');propose.add_argument('--model')
    agent=sub.add_parser('run-agent',help='按原流程owner分派已绑定内部程序；不自动跨owner执行')
    agent.add_argument('agent_id');agent.add_argument('--workspace',required=True);agent.add_argument('--operation-id',required=True)
    agent.add_argument('--procedure',help='默认原流程主方法；辅助/诊断必须显式选择')
    agent.add_argument('--step-id',action='append',dest='step_ids',help='可重复；如sq_rabi_time:q0:seed')
    agent.add_argument('--seed',type=int,default=20261024)
    step=sub.add_parser('run-step',help='低层physical_procedure兼容入口，不是独立智能体')
    step.add_argument('step_id');step.add_argument('--workspace',required=True);step.add_argument('--operation-id',required=True);step.add_argument('--seed',type=int,default=20261024)
    args=parser.parse_args(argv)
    try:
        if args.command in ('list','show','plan','propose'):
            from .advisor import catalog,rule_proposal,openai_proposal
            agents=catalog()
            if args.command=='list':
                emit([{'id':i,'name':a['name'],'kind':a['kind'],'domain':a['domain'],'execution_status':a.get('execution_status','contract_only')} for i,a in agents.items()])
            elif args.command=='show':emit(agents[args.agent_id])
            elif args.command=='plan':
                from .planning import plan,affected_agents
                emit({'mode':'planning_only','plan':affected_agents(args.changed,agents) if args.changed else plan(args.domain,agents)})
            else:
                context=json.loads(Path(args.context).read_text(encoding='utf-8'))
                if not isinstance(context,dict):raise ValueError('context必须是JSON对象')
                if args.agent_id not in agents:raise ValueError('未知公开角色；procedure ID不能用于propose')
                if agents[args.agent_id]['kind']=='experiment':
                    from .workflow import FlowAgent
                    flow=FlowAgent(args.agent_id,agents=agents)
                    context={**context,'allowed_parameters':flow.parameter_scope(context)}
                    proposal=(flow.propose(context) if args.provider=='rules' else openai_proposal(args.agent_id,context,model=args.model,agents=agents))
                else:proposal=(rule_proposal(args.agent_id,context,agents) if args.provider=='rules' else openai_proposal(args.agent_id,context,model=args.model,agents=agents))
                emit({'provider':args.provider,'execution':'advice_only','proposal':proposal})
        elif args.command=='demo':
            from .scenario import run_demo
            emit(run_demo(args.output))
        elif args.command=='twin':
            from .twin import run_twin
            qubits=tuple(map(int,args.qubits.split(',')))
            if not qubits or len(set(qubits))!=len(qubits) or set(qubits)-{0,1}:raise ValueError('qubits仅可为0、1或0,1且不重复')
            result=run_twin(args.output,qubits=qubits,shots=args.shots,seed=args.seed,operation_id=args.operation_id,
                            progress=lambda r:print(json.dumps(r,ensure_ascii=False),file=sys.stderr,flush=True))
            from .workflow import WorkflowSupervisor
            grouped=WorkflowSupervisor().group_report(result)
            save(Path(args.output)/'agent_execution_report.json',grouped)
            okay=all(r['state']=='completed' for r in result['runs']) and all(result['valid_evidence'].values()) and len(result['runs'])==1+5*len(qubits)
            emit({'passed':okay,'data_kind':'simulated','scope':'internal_physical_procedures_only','steps':len(result['runs']),
                  'overall_workflow_complete':False,'agent_report':str(Path(args.output)/'agent_execution_report.json'),
                  'summary':str(Path(args.output)/'twin_summary.json'),'states':{r['step_id']:r['state'] for r in result['runs']}})
            return 0 if okay else 1
        elif args.command=='cz':
            from .cz_suite import run_cz
            with redirect_stdout(sys.stderr):result=run_cz(args.output,args.seed)
            emit(result);return 0 if result['passed'] else 1
        elif args.command=='status':
            from .runtime import Runtime
            if not Path(args.db).is_file():raise ValueError('状态库不存在')
            runtime=Runtime(args.db)
            try:emit({'parameters':runtime.get_parameters(),'runs':runtime.list_runs()})
            finally:runtime.close()
        elif args.command=='resume':
            from .runtime import Runtime
            from .twin import QuantumTwinBackend
            from .workflow import WorkflowSupervisor
            if not Path(args.db).is_file():raise ValueError('状态库不存在')
            runtime=Runtime(args.db)
            try:
                result=WorkflowSupervisor().resume_run(runtime,QuantumTwinBackend(args.backend_dir,seed=args.seed),args.run_id);emit(result)
                return 0 if result['state']=='completed' else 1
            finally:runtime.close()
        elif args.command=='run-agent':
            from .runtime import Runtime,ContractError
            from .twin import QuantumTwinBackend,twin_steps
            from .workflow import WorkflowSupervisor
            supervisor=WorkflowSupervisor()
            if args.agent_id not in supervisor.agents:raise ContractError('run-agent只接受15个原流程owner')
            flow=supervisor.agents[args.agent_id]
            procedure=args.procedure or flow.main_procedure_id
            if procedure not in flow.procedure_ids:raise ContractError('PROCEDURE_NOT_OWNED')
            if procedure not in flow.bindings:raise ContractError('UNSUPPORTED_MAIN_METHOD' if procedure==flow.main_procedure_id else 'UNSUPPORTED_PROCEDURE')
            workspace=Path(args.workspace);db=workspace/'runtime.sqlite'
            backend=QuantumTwinBackend(workspace/'backend',seed=args.seed)
            runtime=Runtime(db,() if db.exists() else twin_steps((0,1)))
            try:
                result=supervisor.run_agent(args.agent_id,runtime,backend,args.operation_id,procedure_id=procedure,step_ids=args.step_ids)
                save(workspace/'agent_runs'/(hashlib.sha256(args.operation_id.encode()).hexdigest()+'.json'),result)
                emit(result);return 0 if result['state']=='partial_completed' else 1
            finally:runtime.close()
        elif args.command=='run-step':
            from .runtime import Runtime,ContractError
            from .twin import QuantumTwinBackend
            workspace=Path(args.workspace);db=workspace/'runtime.sqlite'
            if not db.is_file():raise ContractError('先由twin命令建立冻结工作流；未绑定角色不能直接运行')
            runtime=Runtime(db)
            try:
                if args.step_id not in runtime.steps:raise ContractError('UNSUPPORTED_CAPABILITY：此步骤未绑定到本工作区')
                result=runtime.agents[args.step_id].execute(runtime,QuantumTwinBackend(workspace/'backend',seed=args.seed),args.operation_id)
                emit({'scope':'low_level_physical_procedure','original_step_complete':False,'result':result})
                return 0 if result['state']=='completed' else 1
            finally:runtime.close()
        return 0
    except (ValueError,KeyError,RuntimeError,OSError,TypeError) as exc:
        print(json.dumps({'error_type':type(exc).__name__,'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
