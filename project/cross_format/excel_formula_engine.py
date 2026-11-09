"""Offline evaluator of the workbook's actual standard Excel formula ASTs.

Not Microsoft Excel. Supports this deliverable's scalar and vector formulas;
unknown functions fail instead of being substituted with expected answers.
Independent R/Python oracles check the evaluated outputs.
"""
import math,numbers,re
import numpy as np
from openpyxl.formula import Tokenizer
from openpyxl.utils import range_boundaries,get_column_letter

class FormulaError(Exception):pass

class Parser:
    precedence={'=':1,'<>':1,'<':1,'>':1,'<=':1,'>=':1,'&':2,'+':3,'-':3,'*':4,'/':4,'^':5}
    def __init__(self,text):self.tokens=[t for t in Tokenizer(text).items if t.type!='WHITE-SPACE'];self.i=0
    def parse(self,minimum=0):
        t=self.tokens[self.i];self.i+=1
        if t.type=='OPERATOR-PREFIX':node=('unary',t.value,self.parse(6))
        elif t.type=='FUNC' and t.subtype=='OPEN':
            args=[]
            if not(self.tokens[self.i].type=='FUNC' and self.tokens[self.i].subtype=='CLOSE'):
                while True:
                    args.append(self.parse())
                    if self.tokens[self.i].type=='SEP':self.i+=1;continue
                    break
            self.i+=1;node=('function',t.value[:-1].upper(),args)
        elif t.type=='PAREN' and t.subtype=='OPEN':node=self.parse();self.i+=1
        elif t.type=='OPERAND':
            if t.subtype=='NUMBER':node=('literal',float(t.value))
            elif t.subtype=='TEXT':node=('literal',t.value[1:-1].replace('""','"'))
            elif t.subtype=='LOGICAL':node=('literal',t.value=='TRUE')
            elif t.subtype=='RANGE':node=('reference',t.value)
            else:raise FormulaError('Unsupported operand '+str(t))
        else:raise FormulaError('Unsupported token '+str(t))
        while self.i<len(self.tokens):
            op=self.tokens[self.i]
            if op.type!='OPERATOR-INFIX' or self.precedence.get(op.value,-1)<minimum:break
            self.i+=1;p=self.precedence[op.value];right=self.parse(p if op.value=='^' else p+1);node=('binary',op.value,node,right)
        return node

def excel_text(x):
    if x is None:return ''
    if isinstance(x,(bool,np.bool_)):return 'TRUE' if x else 'FALSE'
    if isinstance(x,(float,np.floating)) and float(x).is_integer():return str(int(x))
    return str(x)

class Engine:
    def __init__(self,workbook):self.wb=workbook;self.cache={};self.ranges={};self.ast={};self.evaluated_formulas=0
    def reset(self):self.cache.clear();self.ranges.clear()
    def set(self,sheet,address,value):self.wb[sheet][address]=value;self.reset()
    def cell(self,sheet,address):
        address=address.replace('$','');key=(sheet,address)
        if key in self.cache:return self.cache[key]
        v=self.wb[sheet][address].value
        if isinstance(v,str) and v.startswith('='):
            origin=int(re.search(r'\d+$',address).group())
            # The deliverable uses <20,000 rows. Normalize relative row references
            # to compile each repeated row template once, preserving absolute rows.
            chunks=re.split(r'("(?:[^"]|"")*")',v)
            for i in range(0,len(chunks),2):
                chunks[i]=re.sub(r'(?<![A-Za-z0-9_$])(\$?[A-Z]{1,3})(\d+)',lambda m:m[1]+str(500000+int(m[2])-origin),chunks[i])
            normalized=''.join(chunks)
            if normalized not in self.ast:
                parser=Parser(normalized);node=parser.parse()
                if parser.i!=len(parser.tokens):raise FormulaError('Trailing formula tokens '+v)
                self.ast[normalized]=node
            try:v=self.eval(self.ast[normalized],sheet,origin)
            except Exception as e:raise FormulaError(f'{sheet}!{address}: {e}') from e
            self.evaluated_formulas+=1
        if v is None:v=''
        if isinstance(v,np.generic):v=v.item()
        self.cache[key]=v;return v
    def reference(self,reference,sheet,origin):
        if '!' in reference:sheet,reference=reference.rsplit('!',1);sheet=sheet.strip("'").replace("''", "'")
        reference=reference.replace('$','')
        reference=re.sub(r'([A-Z]{1,3})(\d+)',lambda m:m[1]+str(int(m[2])-500000+origin) if 450000<int(m[2])<550000 else m[0],reference)
        if ':' not in reference:return self.cell(sheet,reference)
        key=(sheet,reference)
        if key in self.ranges:return self.ranges[key]
        a,b,c,d=range_boundaries(reference)
        values=[[self.cell(sheet,f'{get_column_letter(j)}{i}') for j in range(a,c+1)] for i in range(b,d+1)]
        result=np.array(values,dtype=object)
        # Homogeneous numeric vectors run in NumPy without object arithmetic.
        try:result=result.astype(float)
        except (ValueError,TypeError):pass
        self.ranges[key]=result;return result
    def eval(self,node,sheet,origin):
        kind=node[0]
        if kind=='literal':return node[1]
        if kind=='reference':return self.reference(node[1],sheet,origin)
        if kind=='unary':
            v=self.eval(node[2],sheet,origin)
            return np.asarray(v,dtype=float)*(1 if node[1]=='+' else -1) if isinstance(v,np.ndarray) else float(v)*(1 if node[1]=='+' else -1)
        if kind=='binary':
            op=node[1];a=self.eval(node[2],sheet,origin);b=self.eval(node[3],sheet,origin)
            if op=='&':return excel_text(a)+excel_text(b)
            if op in ['=','<>','<','>','<=','>=']:
                fn={'=':np.equal,'<>':np.not_equal,'<':np.less,'>':np.greater,'<=':np.less_equal,'>=':np.greater_equal}[op]
                if isinstance(a,str) and isinstance(b,str):a,b=a.casefold(),b.casefold()
                return fn(a,b)
            if isinstance(a,str) and a=='':a=0
            if isinstance(b,str) and b=='':b=0
            with np.errstate(divide='raise',invalid='raise',over='raise'):
                if op=='+':return a+b
                if op=='-':return a-b
                if op=='*':return a*b
                if op=='/':return a/b
                if op=='^':return a**b
            raise FormulaError('Unsupported operator '+op)
        name=node[1];args=node[2]
        if name=='IF':
            cond=self.eval(args[0],sheet,origin)
            if isinstance(cond,np.ndarray):return np.where(cond,self.eval(args[1],sheet,origin),self.eval(args[2],sheet,origin))
            return self.eval(args[1] if bool(cond) else args[2],sheet,origin)
        if name=='IFERROR':
            try:return self.eval(args[0],sheet,origin)
            except (FormulaError,ValueError,TypeError,IndexError,ZeroDivisionError,FloatingPointError):return self.eval(args[1],sheet,origin)
        values=[self.eval(a,sheet,origin) for a in args]
        flat=lambda:np.concatenate([np.asarray(v,dtype=object).reshape(-1) for v in values])
        if name=='AND':return all(bool(x) for x in flat())
        if name=='OR':return any(bool(x) for x in flat())
        if name=='NOT':return not bool(values[0])
        if name=='COUNT':return sum(isinstance(x,numbers.Number) and not isinstance(x,(bool,np.bool_)) for x in flat())
        if name=='LEN':return len(excel_text(values[0]))
        if name=='VALUE':
            if excel_text(values[0]).strip()=='':raise FormulaError('VALUE of empty text')
            return float(values[0])
        if name=='EXACT':return excel_text(values[0])==excel_text(values[1])
        if name=='INDEX':return np.asarray(values[0],dtype=object).reshape(-1)[int(values[1])-1]
        if name=='MATCH':
            arr=np.asarray(values[1],dtype=object).reshape(-1);key=excel_text(values[0]).casefold()
            found=[i for i,v in enumerate(arr) if excel_text(v).casefold()==key]
            if not found:raise FormulaError('MATCH not found')
            return found[0]+1
        if name in ['SUMIFS','COUNTIFS','COUNTIF']:
            if name=='SUMIFS':source=np.asarray(values[0],dtype=float).reshape(-1);pairs=values[1:]
            else:source=None;pairs=values
            mask=None
            for array,criterion in zip(pairs[::2],pairs[1::2]):
                array=np.asarray(array,dtype=object).reshape(-1)
                if isinstance(criterion,str):m=np.array([excel_text(x).casefold()==criterion.casefold() for x in array])
                else:m=np.array([x==criterion for x in array])
                mask=m if mask is None else mask&m
            return float(source[mask].sum()) if source is not None else int(mask.sum())
        if name in ['SUM','MIN','MAX']:
            array=np.array([x for x in flat() if isinstance(x,numbers.Number)],dtype=float)
            return float({'SUM':np.sum,'MIN':np.min,'MAX':np.max}[name](array))
        if name=='SUMPRODUCT':
            arrays=[np.asarray(v,dtype=float) for v in values];result=arrays[0]
            for a in arrays[1:]:result=result*a
            return float(np.sum(result))
        if name in ['LN','EXP','ABS']:
            value=values[0]
            if name=='LN' and np.any(np.asarray(value,dtype=float)<=0):raise FormulaError('LN domain')
            result={'LN':np.log,'EXP':np.exp,'ABS':np.abs}[name](value)
            return result.item() if np.ndim(result)==0 else result
        raise FormulaError('Unsupported function '+name)
