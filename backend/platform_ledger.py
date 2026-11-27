"""Keep the vendor's simulated runner and its historical StepSpecs intact.

The platform uses its SQLite parameter/dependency ledger but dispatches every
real measurement through Controller. Only the controller may consume a smart
workflow's hardware permit.
"""
from dataclasses import dataclass, replace
import math

from quantum_agents.runtime import Budget, ContractError, Runtime, StepAgent, StepSpec


@dataclass(frozen=True)
class AccountingBudget(Budget):
    max_attempts: int | None = None
    accounting_only: bool = True

    def __post_init__(self):
        if (self.accounting_only is not True or type(self.shots) is not int or self.shots < 1
                or type(self.timeout_s) not in (int,float) or not math.isfinite(self.timeout_s) or self.timeout_s <= 0
                or any(v is not None for v in (self.max_attempts,self.total_shots,self.max_total_shots,self.max_acquisitions))):
            raise ContractError('仅记账步骤须保留有效单次采集参数且不设置累计额度')


class PlatformRuntime(Runtime):
    @staticmethod
    def _spec(value):
        budget = value['budget']
        if budget.get('accounting_only') is True:
            return StepSpec(**{**value, 'budget':AccountingBudget(**budget)})
        return Runtime._spec(value)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_accounting_workflows'").fetchone():
            return
        # Explicit conversion updates the workflow authorization; preserve its
        # originally registered StepSpec and historical operation fingerprints.
        owners = {r[0] for r in self.db.execute('SELECT workflow_id FROM platform_accounting_workflows')}
        if not owners:
            return
        for key, spec in self.steps.items():
            if key.partition('/')[0] in owners and not isinstance(spec.budget, AccountingBudget):
                budget = AccountingBudget(shots=spec.budget.shots, timeout_s=spec.budget.timeout_s)
                self.steps[key] = replace(spec, budget=budget)
                self.agents[key] = StepAgent(self.steps[key])

    def run_step(self, step_id, backend, operation_id):
        spec = self.steps.get(step_id)
        if spec and isinstance(spec.budget, AccountingBudget):
            raise ContractError('真机智能任务通过平台控制器核验设备租约并下发')
        return super().run_step(step_id, backend, operation_id)
