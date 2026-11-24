from .qtypes import Node
from .errors import * 
from .config import debug_mode
from .decompose import create_gate
import numpy as np
class Visitor():
    """
    抽象语法树遍历类
        qvar: 量子变量字典，记录每个变量对应的数组长度
        qmap: 量子变量字典，记录每个变量对应物理比特的起始下标
        qcnt: 量子比特总数
        cvar: 经典变量字典，记录每个变量对应的数组长度
        cmap: 经典变量字典，记录每个变量对应物理比特的起始下标
        ccnt: 经典变量总数
        gate：所有可用门字典，记录门作用比特数和所需参数个数
        defined_gate：用户自定义门字典
    """
    
    def __init__(self, allow_undefined = False) -> None:
        
        self.qvar = {}
        self.qmap = {}
        self.qcnt = 0
        self.cvar = {}
        self.cmap = {}
        self.ccnt = 0
        self.gate = {
            'h': (1, 0),
            'x': (1, 0), 
            'y': (1, 0),
            'z': (1, 0),
            's': (1, 0),
            'sdg': (1, 0),
            't': (1, 0),
            'tdg': (1, 0),
            'rx': (1, 1),
            'ry': (1, 1),
            'rz': (1, 1),
            'cx': (2, 0),
            'cy': (2, 0),
            'cz': (2, 0),
            'ch': (2, 0),
            'crx': (2, 1),
            'cry': (2, 1),
            'crz': (2, 1),
            'ccx': (3, 0),
            'u1': (1, 1),
            'u2': (1, 2),
            'u3': (1, 3),
        }
        self.defined_gate = {}
        self.now_gate = ''
        self.ingate = False
        self.meas = []
        self.res = []
        self.allow_undefined = allow_undefined
    
    def add_reg(self, id, num, type, pos):
        """
        添加变量到相关字典中

        Args:
            id (_type_): 变量名
            num (_type_): 变量的长度
            type (_type_): 类型
            pos (_type_): 位置
        """
        dic = self.qvar
        if type == 'creg':
            dic = self.cvar    
        
        if id in dic: ErrorThrow(f'in line {pos}, {type} redefined')
        dic[id] = num
        
        if type == 'qreg':
            self.qmap[id] = self.qcnt
            self.qcnt += num
        else:
            self.cmap[id] = self.ccnt
            self.ccnt += num
    
    def check_qreg(self, arg, pos):
        """
        检查量子变量使用是否越界，若没有越界则返回其对应的物理比特下标，否则报错
        """
        id = arg[0]
        if id not in self.qvar: ErrorThrow(f'in line {pos}, qreg {id} is not defined')
        if len(arg) == 2:
            idx = arg[1]
            if idx >= self.qvar[id]: ErrorThrow(f'in line {pos}, qreg {id} out of bound')
            return [self.qmap[id] + idx]
        else:
            return list(range(self.qmap[id], self.qmap[id] + self.qvar[id]))

    def check_creg(self, arg, pos):
        """
        检查经典变量使用是否越界，若没有越界则返回其对应的物理比特下标，否则报错
        """
        id = arg[0]
        if id not in self.cvar: ErrorThrow(f'in line {pos}, creg {id} is not defined')
        if len(arg) == 2:
            idx = arg[1]
            if idx >= self.cvar[id]: ErrorThrow(f'in line {pos}, creg {id} out of bound')
            return [self.cmap[id] + idx]
        else:
            return list(range(self.cmap[id], self.cmap[id] + self.cvar[id]))
    
    def check_in_gate_qubit(self, qubit, pos):
        """
        检查门作用的量子比特是否已经定义
        """
        if len(qubit) != 1: ErrorThrow(f'in line {pos}, qubit is not defined')
        if qubit[0] not in self.defined_gate[self.now_gate]['gate_q']: ErrorThrow(f'in line {pos}, qubit {qubit[0]} is not defined')
        
    def check_qlist(self, qids, pos):
        """
        检查量子比特是否重复使用在同一操作中
        """
        if len(set(qids)) != len(qids): ErrorThrow(f'in line {pos}, qubit reused')
    
    def visitProgram(self, s: Node):
        """
        遍历抽象语法树的入口函数，通过dfs方法遍历根节点的所有子节点来完成遍历
        """
        assert s.type == 'top'
        
        self.res = []
        
        for stat in s.children:
            self.visitState(stat)
        
        return self.qcnt, self.res
    
    def visitState(self, s: Node):
        """
        遍历所有语句，openqasm2.0中语句主要包括
            变量定义: 更新相关成员变量，将其添加至相关字典中
            门定义: 更新相关成员变量，将其添加至相关字典中
            量子操作: 递归遍历
        """
        if s.type == 'defvar':
            t = s.leaf
            id, num = s.children
            self.add_reg(id, num, t, s.pos)
        elif s.type == 'defgate':
            id = s.leaf[0]
            if id in self.gate: ErrorThrow(f'in line {s.pos}, {id} redefined')
            self.gate[id] = (len(s.leaf[1]), len(s.leaf[2]))
            self.defined_gate[id] = {
                'gate_q': s.leaf[1],
                'gate_param': s.leaf[2],
                'base_gate': []
            }
            self.ingate = True
            self.now_gate = id
            for gop in s.children:
                self.visitQop(gop)
            self.ingate = False
        elif s.type == 'barrier':
            self.visitBarrier(s)
        elif s.type == 'if':
            c = s.children[0]
            v = s.children[1]
            # 判断id是否为定义的creg
            if c not in self.cvar:
                ErrorThrow(f"in line {s.pos}, creg {c} is not defined")
            bit_length = self.cvar[c]
            if v > int('1'*bit_length, 2):
                ErrorThrow(f"in line {s.pos}, value {v} if always larger than creg {c}")
            pass
        else:
            self.visitQop(s)
    
    def visitBarrier(self, s:Node):
        """
        检查量子比特已定义且无重复，若在自定义门中则添加相关元组以便后续解析，否则直接添加同步操作的中间表示到结果列表中
        """
        qids = []
        for qubit in s.children:
            if self.ingate:
                self.check_in_gate_qubit(qubit, s.pos)
                qids.append(qubit[0])
            else:
                qlist = self.check_qreg(qubit, s.pos)
                qids += qlist
        
        self.check_qlist(qids, s.pos)
        if self.ingate:
            self.defined_gate[self.now_gate]['base_gate'].append(('barrier', [], qids))
        else:
            self.res.append(create_gate('barrier', qids))
    
    def visitQop(self, s: Node):
        """
        量子操作分为三种
            同步: 递归遍历
            测量: 测量操作首先判断量子变量和经典变量是否可用，且比特没有重复测量，在结果中添加测量的中间表示
            门操作: 递归遍历
        """
        if s.type == 'measure':
            q, c = s.children, s.leaf
            qlist = self.check_qreg(q, s.pos)
            clist = self.check_creg(c, s.pos)
            if len(qlist) != len(clist): ErrorThrow(f"in line {s.pos}, the length of qregs and cregs is different")
            for qubit in qlist:
                if qubit in self.meas: ErrorThrow(f"in line {s.pos}, multiple measurements")
                self.meas.append(qubit)
                self.res.append(create_gate('measure', [qubit]))
        elif s.type == 'barrier':
            self.visitBarrier(s)
        else:
            self.visitUop(s)
    
    def visitUop(self, s: Node):
        """
        首先检查门是否在可用门字典中
        然后检查参数数量是否与门需要的一致，以及量子比特是否已定义且无重复
        最后若调用时在自定义门中则添加相关元组以便后续解析，否则通过eval_gate添加量子门操作对应的中间表示到结果列表中。
        """
        uid = s.children[0]
        if not self.allow_undefined:
            if uid not in self.gate: ErrorThrow(f"in line {s.pos}, gate {uid} is not defined")
        
        if uid in self.gate:
            qnum, pnum = self.gate[uid]
            if pnum != len(s.children[1]):
                ErrorThrow(f"in line {s.pos}, parameter error, need {pnum} but {len(s.children[1])}")
            
            if qnum != len(s.leaf): ErrorThrow(f"in line {s.pos}, qubit error, need {qnum} but {len(s.leaf)}")

        if self.ingate:
            qids = []
            for qubit in s.leaf:
                self.check_in_gate_qubit(qubit, s.pos)
                qids.append(qubit[0])
            
            self.check_qlist(qids, s.pos)
            self.defined_gate[self.now_gate]['base_gate'].append((uid, s.children[1], qids))
        else:
            qreg_num = -1
            qids = []
            for qubit in s.leaf:
                if len(qubit) == 1:
                    idlist = self.check_qreg(qubit, s.pos)
                    if qreg_num != -1 and len(idlist) != qreg_num: 
                        ErrorThrow(f"in line {s.pos}, qreg length should be the same")
                    qreg_num = len(idlist)
                    qids.append(idlist)
                else:
                    if len(qubit) != 2: ErrorThrow(f"in line {s.pos}, need specific qubit")
                    qids.append([self.check_qreg(qubit, s.pos)[0]])
            
            if qreg_num == -1: qreg_num = 1
            for i in range(qreg_num):
                targets = []
                for qubits in qids:
                    if len(qubits) == 1:
                        targets.append(str(qubits[0]))
                    else:
                        targets.append(str(qubits[i]))
                
                self.check_qlist(targets, s.pos)
                self.eval_gate(uid, s.children[1], targets)
    
    def eval_gate(self, uid, args, qids, dic = None):
        """
        解析自定义门中的子语句
        如果是全局的量子操作，则直接添加对应的中间表示
        否则生成参数对应字典，该字典的键值对为自定义门的参数以及调用时实际使用值，用该字典以及eval函数来实现内联操作，将自定义门中的子语句转换为中间表示。
        """
        real_args = []
        for arg in args:
            real_args.append(eval(arg, dic))
        real_qids = []
        for qid in qids:
            real_qids.append(eval(qid, dic))
        
        if uid not in self.defined_gate:
            args = real_args
            if len(real_args) == 1: args = real_args[0]
            self.res.append(create_gate(uid, real_qids, args, self.allow_undefined))
        else:
            for _uid, _args, _qids in self.defined_gate[uid]['base_gate']:
                _dic = {}
                for param, val in zip(self.defined_gate[uid]['gate_param'], real_args):
                    _dic[param] = val
                for qreg, val in zip(self.defined_gate[uid]['gate_q'], real_qids):
                    _dic[qreg] = val
                self.eval_gate(_uid, _args, _qids, _dic)