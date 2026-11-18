from ply import (
    lex,
    yacc
)
from . import tokrules
from .tokrules import tokens
from .config import debug_mode
from .errors import *
import time
from .qtypes import * 
from .visitor import Visitor
from .decompose import transpiler, optimizer

gate_params = []

# 顶层文法

def p_mainprogram(p):
    '''mainprogram : OPENQASM REAL ';' incfile ';' program'''
    #'''program : programDef programBody'''
    
    if p[2] != 2.0: raise CompileError(1001, f"in line {p.lineno}: version error")
    p[0] = Node("top", p[6], None, p.lineno(6))

def p_incfile(p):
    '''incfile : INCLUDE '"' STDFILE '"' '''
    p[0] = p[3]
    
def p_program(p):
    '''program : statement
                | program statement'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        #p[0] = p[1] + [p[2]]
        p[1].append(p[2])
        p[0] = p[1]

def p_statement(p):
    '''statement : decl 
                  | gatedecl
                  | qop
                  | BARRIER qlist ';'
                  | ifop
    '''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = Node('barrier', p[2], None, p.lineno(1))
    
def p_decl(p):
    '''decl : QREG ID '[' NUMBER ']' ';'
            | CREG ID '[' NUMBER ']' ';' '''

    p[0] = Node("defvar", [p[2], p[4]], p[1], p.lineno(2))

def p_gatedecl(p):
    '''gatedecl : GATE ID idlist '{' goplist '}'
                | GATE ID '(' idlist ')' idlist '{' goplist '}' '''
    if len(p) == 7:
        p[0] = Node('defgate', p[5], [p[2], p[3], []], p.lineno(2))    
    else:
        p[0] = Node('defgate', p[8], [p[2], p[6], p[4]], p.lineno(2)) 
        
def p_idlist(p):
    '''idlist : ID 
                | idlist ',' ID '''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[3])
        p[0] = p[1]
        #p[0] = p[1] + [p[3]]

def p_goplist(p):
    '''goplist : uop
                | BARRIER idlist ';'
                | goplist uop
                | goplist BARRIER idlist ';'
    '''
    if len(p) == 2:
        p[0] = [p[1]]
    elif len(p) == 3:
        #p[0] = p[1] + [p[2]]
        p[1].append(p[2])
        p[0] = p[1]
    elif len(p) == 4:
        p[0] = [Node('barrier', p[2], None, p.lineno(1))]
    else:
        #p[0] = p[1] + [Node('barrier', p[3], None, p.lineno(2))]
        p[1].append(Node('barrier', p[3], None, p.lineno(2)))
        p[0] = p[1]
        
def p_qop(p):
    '''qop : uop
            | MEASURE argument ARROW argument ';' '''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = Node('measure', p[2], p[4], p.lineno(1))

def p_argument(p):
    '''argument : ID 
                | ID '[' NUMBER ']' '''

    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[0] = [p[1], p[3]]

def p_qlist(p):
    '''qlist : argument
            | qlist ',' argument '''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        #p[0] = p[1] + [p[3]]
        p[1].append(p[3])
        p[0] = p[1]
        
def p_uop(p):
    '''uop : ID qlist ';'
            | ID '(' explist ')' qlist ';' '''
    if len(p) == 4:
        p[0] = Node("uop", [p[1], []], p[2], p.lineno(1))
    else:
        p[0] = Node("uop", [p[1], p[3]], p[5], p.lineno(1))
    
def p_explist(p):
    '''explist : exp
                | explist ',' exp '''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        #p[0] = p[1] + [p[3]]
        p[1].append(p[3])
        p[0] = p[1]

def p_exp(p):
    '''exp : REAL
            | NUMBER
            | PI
            | ID
            | '(' exp ')'
            | exp '+' exp
            | exp '-' exp
            | exp '*' exp
            | exp '/' exp
            | '-' exp 
            | unaryop '(' exp ')' '''
    if len(p) == 2:
        if p[1] == 'pi': p[0] = 'np.pi'
        else: p[0] = str(p[1])
    elif len(p) == 3:
        p[0] = f'-{p[2]}'
    elif len(p) == 4:
        if p[1] == '(':
            p[0] = f'({p[2]})'
        else:
            p[0] = f'{p[1]}{p[2]}{p[3]}'
    else:
        if p[1] == 'sin':
            p[0] = f'np.sin({p[3]})'
        elif p[1] == 'cos':
            p[0] = f'np.cos({p[3]})'
        elif p[1] == 'tan':
            p[0] = f'np.tan({p[3]})'
        elif p[1] == 'exp':
            p[0] = f'np.exp({p[3]})'
        elif p[1] == 'ln':
            p[0] = f'np.log({p[3]})'
        elif p[1] == 'sqrt':
            p[0] = f'np.sqrt({p[3]})'
        else:
            raise CompileError(1001, f"in line {p.lineno}, {p[1]} is not a legal operator")
    
def p_unaryop(p):
    '''unaryop : SIN
                | COS
                | TAN
                | EXP
                | LN
                | SQRT '''
    p[0] = p[1]

def p_ifop(p):
    '''ifop : IF '(' ID EQ NUMBER ')' qop
    '''
    p[0] = Node("if", [p[3], p[5]], p[7], p.lineno(1))

def p_error(p):
    if isinstance(p, lex.LexToken):
        raise CompileError(1001, "in line {}, can not parser the sentence at token: '{}'".format(p.lineno, p.value))
    else:
        raise CompileError(1001, "lack ';' or '}' at the end of code")
    #ErrorThrow("Syntax error in line " + str(p.lineno))

        
def get_abs_tree(data):
    """
    解析OpenQasm2.0，得到抽象语法树

    Args:
        data (_type_): OpenQasm2.0
    """
    lexer = lex.lex(module = tokrules)
    lexer.input(data)
    #parser = yacc.yacc(debug=True)
    parser = yacc.yacc(debug=False, write_tables=False)
    return parser.parse(data)

def get_ir(abs_tree):
    """
    解析抽象语法树，得到中间表示，其为Gate列表

    Args:
        abs_tree (_type_): 抽象语法树
    """
    vist = Visitor()
    return vist.visitProgram(abs_tree)

def transpile_ir(ir):
    """
    将基础门转换到RX,RY,RZ,CZ上，并做门优化

    Args:
        ir: 中间表示
    """
    opt_ir = optimizer(ir)
    trans_ir = transpiler(opt_ir)
    opt_ir = optimizer(trans_ir)
    return opt_ir