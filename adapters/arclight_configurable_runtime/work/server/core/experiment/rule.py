from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Protocol, Tuple
import re


def get_by_dotted(obj: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for k in dotted.split("."):
        if not isinstance(cur, Mapping) or k not in cur:
            raise KeyError(f"Missing path segment '{k}' in '{dotted}'")
        cur = cur[k]
    return cur

def set_by_dotted(obj: Dict[str, Any], dotted: str, value: Any) -> None:
    cur: Any = obj
    keys = dotted.split(".")
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value

def format_path(template: str, binding: Mapping[str, str]) -> str:
    return template.format(**binding)


# =========================
# constant / selector
# =========================

Binding = Dict[str, str]

class Selector(Protocol):
    """Return a list of bindings used to instantiate symbolic placeholders."""
    def select(self, cfg_point: Mapping[str, Any]) -> List[Binding]:
        ...

@dataclass(frozen=True)
class StaticSelector:
    """Always return the same bindings (useful for global/non-iterated rules)."""
    bindings: List[Binding]

    def select(self, cfg_point: Mapping[str, Any]) -> List[Binding]:
        return list(self.bindings)

@dataclass(frozen=True)
class KeyRegexSelector:
    """
    Enumerate keys under some base path that match a regex, and bind them to a var name.
    Example:
      base="", regex=r"Q\\d+", var="q"  -> [{"q":"Q1"},{"q":"Q2"},...]
    """
    base: str
    regex: str
    var: str

    def select(self, cfg_point: Mapping[str, Any]) -> List[Binding]:
        base_obj = get_by_dotted(cfg_point, self.base) if self.base else cfg_point
        if not isinstance(base_obj, Mapping):
            return []
        pat = re.compile(self.regex)
        out: List[Binding] = []
        for k in base_obj.keys():
            if isinstance(k, str) and pat.fullmatch(k):
                out.append({self.var: k})
        return out


# =========================
# rule
# =========================

@dataclass(frozen=True)
class Rule:
    name: str
    inputs: Tuple[str, ...]                         # path templates
    expr: Callable[..., Any]                        # lambda/func
    output: str                                     # path template
    selector: Selector                             


# =========================
# normalize engine
# =========================

@dataclass
class Normalizer:
    rules: List[Rule]

    def apply(self, cfg_point: Dict[str, Any]):
        
        for rule in self.rules:
            bindings = rule.selector.select(cfg_point)

            for bind in bindings:
                # Render paths
                in_paths = [format_path(p, bind) for p in rule.inputs]
                out_path = format_path(rule.output, bind)
                # Fetch inputs
                try:
                    values = [get_by_dotted(cfg_point, p) for p in in_paths]
                except KeyError:
                    raise

                # Compute
                newv = rule.expr(*values)
                # Write-back
                
                set_by_dotted(cfg_point, out_path, newv)

        return cfg_point
    
    def add_rule(self, rule: Rule):
        self.rules.append(rule)
    
    def clear(self):
        self.rules = []


def default_rules():

    # constant: all qubits matching Q\d+
    select_qubits = KeyRegexSelector(base="", regex=r"Q\d+", var="q")
    
    # rule: drivCarryFreq = drivFreq - drivLoFreq for each qubit
    rule_driv_carry = Rule(
        name="drivCarryFreq",
        inputs=("{q}.para.drivFreq", "{q}.para.drivLoFreq"),
        expr=lambda drivFreq, drivLoFreq: drivFreq - drivLoFreq,
        output="{q}.para.drivCarryFreq",
        selector=select_qubits,
    )

    rule_prob_carry = Rule(
        name="probCarryFreq",
        inputs=("{q}.para.probFreq", "{q}.para.probLoFreq"),
        expr=lambda probFreq, probLoFreq: probFreq - probLoFreq,
        output="{q}.para.probCarryFreq",
        selector=select_qubits,
    )

    return [rule_driv_carry, rule_prob_carry]
