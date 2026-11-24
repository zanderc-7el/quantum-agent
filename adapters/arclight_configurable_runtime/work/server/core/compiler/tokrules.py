from .errors import CompileError

t_EQ  = r'\=\='
t_ignore = ' \t\r'
t_ARROW = r'->'


# 解析错误的时候直接抛出异常
def t_error(t):
    raise CompileError(1000, 'in line {}, lex error at token : {}'.format(t.lineno, t.value[0]))
    #raise Exception('error {} at line {}'.format(t.value, t.lineno))

# 记录行号，方便出错定位
def t_newline(t):
    r'\n+'
    t.lexer.lineno += len(t.value)

# 支持c++风格的\\注释
def t_ignore_COMMENT(t):
    r'\/\/[^\n]*'

# 常数命令规则
def t_REAL(t):
    r'([0-9]+\.[0-9]*|[0-9]*\.[0-9]+)([eE][-+]?[0-9]+)?'
    t.value = float(t.value)
    return t

def t_NUMBER(t):
    r'[1-9]+[0-9]*|0'
    t.value = int(t.value)
    return t

def t_STDFILE(t):
    r'qelib1.inc'
    return t

# 标识符的命令规则
def t_ID(t):
    r'[a-zA-Z_][a-zA-Z_0-9]*'
    t.type = reserved.get(t.value, 'ID')    # Check for reserved words
    return t

reserved = {
    'OPENQASM': 'OPENQASM',
    'include': 'INCLUDE',
    'if': 'IF',
    'qreg': 'QREG',
    'creg': 'CREG',
    'gate': 'GATE',
    'measure': 'MEASURE',
    'pi': 'PI',
    'sin': 'SIN',
    'cos': 'COS',
    'tan': 'TAN',
    'exp': 'EXP',
    'ln': 'LN',
    'sqrt': 'SQRT',
    'barrier': 'BARRIER',
}

# 输入中支持的符号头token，当然也支持t_PLUS = r'\+'的方式将加号定义为token

literals = ['+','-','*','/','%', '<','>','=',',','(',')','[',']','{','}',';',':', '"']

tokens = ['EQ','REAL','NUMBER', 'ID', 'ARROW', 'STDFILE'] + list(reserved.values())
