"""Read original gate identities/angles for @4 without broadening the @3 parser."""
import ast
import math
import re
from dataclasses import replace
from .qasm import QasmOp, QasmProgram, parse_qasm, QasmError

_NATIVE={'rx':('RX','x'),'ry':('RY','y'),'rz':('RZ','z'),'r':('RXY','x'),
         'vz':('VZ','z'),'ms':('MS','cx'),'rxx':('RXX','cx'),'rzz':('RZZ','cz')}

def _angle(text):
    def value(n):
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):return n.value
        if isinstance(n,ast.Name) and n.id=='pi':return math.pi
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            return value(n.operand)*(1 if isinstance(n.op,ast.UAdd) else -1)
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=value(n.left),value(n.right)
            if isinstance(n.op,ast.Add):return a+b
            if isinstance(n.op,ast.Sub):return a-b
            if isinstance(n.op,ast.Mult):return a*b
            return a/b
        raise QasmError('D1 unsupported source angle expression')
    try:
        x=float(value(ast.parse(text.strip(),mode='eval').body))
        if not math.isfinite(x):raise ValueError('nonfinite')
        return x
    except (ValueError,SyntaxError,TypeError,ZeroDivisionError) as exc:
        raise QasmError(f'D1 invalid source angle {text!r}') from exc

def parse_source(text, *, keep_barriers=False):
    """Retain original operation numbering (barriers included) for meta.source_op."""
    transformed=[];info=[]
    for lineno,raw in enumerate(text.splitlines(),1):
        for stmt in raw.split('//',1)[0].split(';'):
            stmt=stmt.strip()
            if not stmt:continue
            if stmt.startswith(('OPENQASM','include','qreg','creg')):
                transformed.append(stmt+';');continue
            if stmt.startswith('barrier'):
                # The base parser discards barriers, but source provenance counts them.
                info.append(('BARRIER',(),lineno));transformed.append('barrier;');continue
            match=re.match(r'([a-zA-Z_]\w*)\s*\(([^)]*)\)\s+(.+)$',stmt)
            if match:
                key=match[1].lower()
                if key not in _NATIVE:raise QasmError(f'D1 unrecognized source gate {key}')
                name,placeholder=_NATIVE[key]
                params=tuple(_angle(x) for x in match[2].split(','))
                info.append((name,params,lineno));transformed.append(placeholder+' '+match[3]+';')
            else:
                info.append((None,(),lineno));transformed.append(stmt+';')
    parsed=parse_qasm('\n'.join(transformed));it=iter(parsed.ops);out=[]
    for name,params,line in info:
        if name=='BARRIER':
            if keep_barriers:out.append(QasmOp('BARRIER',(),None,line))
        else:
            old=next(it);out.append(replace(old,name=name or old.name,line=line,params=params))
    return QasmProgram(parsed.n_qubits,parsed.n_bits,out)
