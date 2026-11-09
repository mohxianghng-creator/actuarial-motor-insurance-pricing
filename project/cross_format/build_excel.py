"""Macro-free Excel scoring and scenario engine from the frozen V2 GLM contract."""
from pathlib import Path
import json,sys,math
import pandas as pd
import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment,Border,Side,Protection
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.chart import BarChart,LineChart,Reference
from openpyxl.comments import Comment
from openpyxl.workbook.properties import CalcProperties
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.utils import get_column_letter as L
from format_paths import WORK

ROOT=Path(__file__).resolve().parents[1];R=ROOT/'r_version';OUT=ROOT/'excel_version'
NAVY='173549';TEAL='087F82';PALE='E4F2EF';BLUE='1765AE';AMBER='FFF1DA';GRAY='607785'

def build():
    OUT.mkdir(exist_ok=True);cfg=json.loads((ROOT/'config.json').read_text());summary=json.loads((ROOT/'results/summary.json').read_text())
    terms=pd.read_csv(R/'portable_model/terms.csv').fillna('');params=pd.read_csv(R/'portable_model/parameters.csv',dtype=str)
    q=pd.read_csv(R/'data/quotes.csv');n=len(q);start=8;end=start+n-1;calcrow=end+5
    wb=Workbook();wb.remove(wb.active)
    names=['Read_Me','Dashboard','Controls','Calculator','Quotes','Market_Live','Scenarios','Robustness','Stress','Margin_Curves','Model','Model_Levels','Python_CV','R_Native_CV','Risk_Budget','Credibility','Checks']
    sheets={name:wb.create_sheet(name) for name in names};formula_cells={name:[] for name in names}
    for ws in sheets.values():
        ws.sheet_view.showGridLines=False;ws.freeze_panes='A8';ws.sheet_properties.pageSetUpPr.fitToPage=True
        ws.page_setup.orientation='landscape';ws.page_setup.paperSize=ws.PAPERSIZE_A4;ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0
        for c in range(1,17):ws.column_dimensions[L(c)].width=13
        ws.merge_cells('A1:P2');ws['A1']=ws.title.replace('_',' ')+' | CAS Motor Pricing V2';ws['A1'].font=Font(name='Calibri',size=19,bold=True,color='FFFFFF');ws['A1'].fill=PatternFill('solid',fgColor=NAVY);ws['A1'].alignment=Alignment(vertical='center');ws.row_dimensions[1].height=24;ws.row_dimensions[2].height=10
        ws.merge_cells('A3:P3');ws['A3']='Research only | Monetary units unspecified | Equal exposure assumed | Model coefficients frozen';ws['A3'].font=Font(size=10,color=GRAY)
        for j,dest in enumerate(['Read_Me','Dashboard','Controls','Calculator'],1):
            cell=ws.cell(4,j,dest.replace('_',' '));cell.hyperlink=f"#'{dest}'!A1";cell.font=Font(size=10,bold=True,color=TEAL)
        ws.sheet_properties.outlinePr.summaryRight=False
    def put(ws,address,value,input=False,fmt=None):
        cell=ws[address];cell.value=value;cell.font=Font(name='Calibri',size=10,color=BLUE if input else NAVY)
        if isinstance(value,str) and value.startswith('='):formula_cells[ws.title].append(address)
        if input:cell.fill=PatternFill('solid',fgColor='EDF5FF');cell.protection=Protection(locked=False)
        if fmt:cell.number_format=fmt
        return cell
    def head(ws,row,labels):
        for j,text in enumerate(labels,1):
            c=ws.cell(row,j,text);c.font=Font(name='Calibri',size=10,bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor=TEAL);c.alignment=Alignment(wrap_text=True)
        ws.row_dimensions[row].height=30
    def imported(name,frame):
        ws=sheets[name];head(ws,7,list(frame.columns))
        for i,row in enumerate(frame.itertuples(index=False,name=None),8):
            for j,value in enumerate(row,1):
                if pd.isna(value):value=None
                cell=ws.cell(i,j,value);cell.font=Font(name='Calibri',size=9,color=NAVY)
                if isinstance(value,float):cell.number_format='0.0000'
        ws.auto_filter.ref=f'A7:{L(len(frame.columns))}{len(frame)+7}';ws.freeze_panes='B8'
    # Editable financial inputs; all other model constants are visibly frozen.
    controls={5:('Money rule',cfg['pricing']['margin_mode']),6:('Flat money allowance',15),7:('Low-band money',10),8:('Middle-band money',20),9:('High-band money',35),10:('Shoulder width',20),11:('Fixed expense',15),12:('Variable expense ratio',.15),13:('Tax ratio',0),14:('Trend factor',1),15:('Development factor',1),16:('Commercial money adjustment',0),19:('Competitor multiplier',1),20:('Competitor risk tilt',0),21:('Price elasticity',5),22:('Bound-loss multiplier',1),23:('Reference conversion',.30),24:('Expected policy capacity',2500),25:('Minimum expected volume',1500),26:('Minimum worst expected contribution',0),29:('Region cohort','All')}
    cw=sheets['Controls'];cw.column_dimensions['A'].width=38;cw.column_dimensions['B'].width=30
    for row,(label,value) in controls.items():put(cw,f'A{row}',label);put(cw,f'B{row}',value,True,fmt='0.0%' if row in [12,13,23] else None)
    put(cw,'A31','Input assumptions valid?');valid='AND(OR(B5="flat_constant",B5="tiered_constant",B5="smooth_tiered_constant"),COUNT(B6:B16)=11,MIN(B6:B9)>=0,B7<=B8,B8<=B9,B10>0,B11>=0,B12>=0,B13>=0,B12+B13<1,B14>0,B15>0,COUNT(B19:B26)=8,B19>0,B21>0,B22>0,B23>0,B23<1,B24>=0,B25>=0)'
    put(cw,'B31','='+valid)
    for row,text in [(33,'Blue cells are editable scenario inputs.'),(34,'Money targets net contribution before commercial action.'),(35,'Gross quote effect = money / (1 - variable expense - tax).'),(36,'Smooth shoulders are nonconstant inside transitions.'),(37,'Competitor reference uses frozen default cost rules.'),(38,'Capacity constrains expected volume, not realized policy counts.'),(39,'Dashboard cohort is selected by B29, not the table autofilter.')]:cw.merge_cells(start_row=row,start_column=1,end_row=row,end_column=9);put(cw,f'A{row}',text)
    modes=DataValidation(type='list',formula1='"flat_constant,tiered_constant,smooth_tiered_constant"');cw.add_data_validation(modes);modes.add(cw['B5'])
    numeric=DataValidation(type='decimal',operator='between',formula1='-100000',formula2='100000',allow_blank=False);numeric.error='Use a numeric scenario assumption.';numeric.showErrorMessage=True
    cw.add_data_validation(numeric)
    for row in controls:
        if row not in [5,29]:numeric.add(cw[f'B{row}'])
    # Frozen scalar and categorical model contract.
    scalar=dict(zip(params.parameter,params.value))
    for key in list(scalar):
        try:scalar[key]=float(scalar[key])
        except ValueError:pass
    for _,t in terms[terms.encoding!='onehot'].iterrows():
        for col,prefix in [('coefficient','coefficient'),('center','center'),('scale','scale'),('imputation_transformed','imputation')]:scalar[prefix+'_'+t.feature]=float(t[col])
    refs=pd.read_csv(R/'portable_model/reference_ranges.csv').fillna('')
    for _,r in refs[refs.kind=='numeric'].iterrows():scalar['minimum_'+r.feature]=r.minimum;scalar['maximum_'+r.feature]=r.maximum
    scalar['median_quote_pure']=float(pd.read_csv(ROOT/'results/quote_detail.csv').pure_premium.median())
    mw=sheets['Model'];mw.column_dimensions['A'].width=36;mw.column_dimensions['B'].width=31
    head(mw,7,['Frozen parameter','Value','','Feature','Encoding','Level','Coefficient','Center','Scale','Imputation'])
    smap={}
    for row,(key,value) in enumerate(scalar.items(),8):put(mw,f'A{row}',key);put(mw,f'B{row}',value,fmt='0.0000000000' if isinstance(value,float) else None);smap[key]=f"Model!$B${row}"
    for row,t in enumerate(terms.itertuples(index=False),8):
        for col,value in enumerate([t.feature,t.encoding,t.level,t.coefficient,t.center,t.scale,t.imputation_transformed],4):put(mw,f'{L(col)}{row}',value,fmt='0.0000000000' if isinstance(value,float) else None)
    term_end=len(terms)+7
    levels=sheets['Model_Levels'];regions=['All',*sorted(q.Region.unique())]
    level_meta={}
    for col,(feature,values) in enumerate([('Region cohort',regions),*[(f,list(terms.loc[(terms.feature==f)&(terms.encoding=='onehot'),'level'])) for f in ['Power','Brand','Gas','Region']]],1):
        put(levels,f'{L(col)}7',feature)
        for row,value in enumerate(values,8):put(levels,f'{L(col)}{row}',value)
        level_meta[feature]=f"'Model_Levels'!${L(col)}$8:${L(col)}${len(values)+7}"
    wb.defined_names.add(DefinedName('RegionCohorts',attr_text=f"'Model_Levels'!$A$8:$A${len(regions)+7}"))
    regionvalidation=DataValidation(type='list',formula1='RegionCohorts');cw.add_data_validation(regionvalidation);regionvalidation.add(cw['B29'])
    M=lambda key:smap[key]
    C=lambda row:f'Controls!$B${row}'
    def money(pure,mode,flat,low,middle,high,width):
        u1=f'MAX(0,MIN(1,({pure}-{M("threshold_1")}+{width}/2)/{width}))';u2=f'MAX(0,MIN(1,({pure}-{M("threshold_2")}+{width}/2)/{width}))'
        smooth=f'{low}+({middle}-{low})*(3*({u1})^2-2*({u1})^3)+({high}-{middle})*(3*({u2})^2-2*({u2})^3)'
        return f'IF({mode}="flat_constant",{flat},IF({mode}="tiered_constant",IF({pure}<{M("threshold_1")},{low},IF({pure}<{M("threshold_2")},{middle},{high})),{smooth}))'
    qw=sheets['Quotes'];headers=['PolicyID','Power','CarAge','DriverAge','Brand','Gas','Region','Density','Z_CarAge','Z_DriverAge','Z_LogDensity','Category_log_effect','Log_frequency','Frequency','Pure_premium','Expected_loss','Shoulder_1','Shoulder_2','Money_allowance','Cost_floor','Technical_premium','Quote','Expected_contribution','Floor_applied','Frozen_reference_price','Log_risk_ratio','Cohort_include','Review_status','Input_valid','Risk_band'];head(qw,7,headers)
    for row,data in enumerate(q.itertuples(index=False,name=None),start):
        for col,value in enumerate(data,1):put(qw,f'{L(col)}{row}',value,input=col>1)
    def quote_formulas(row):
        rawcols={'CarAge':'C','DriverAge':'D','Density':'H'}
        for target,feature in [('I','CarAge'),('J','DriverAge'),('K','Density')]:
            raw=f'{rawcols[feature]}{row}';trans=f'LN(1+VALUE({raw}))' if feature=='Density' else f'VALUE({raw})'
            put(qw,f'{target}{row}',f'=IFERROR((IF(LEN({raw}&"")=0,{M("imputation_"+feature)},{trans})-{M("center_"+feature)})/{M("scale_"+feature)},"")')
        cats=[];known=[]
        for feature,col in [('Power','B'),('Brand','E'),('Gas','F'),('Region','G')]:
            level=f'IF(LEN({col}{row}&"")=0,"Missing",{col}{row})'
            canonical=f'IFERROR(EXACT({level},INDEX({level_meta[feature]},MATCH({level},{level_meta[feature]},0))),FALSE)'
            cats.append(f'IF({canonical},SUMIFS(Model!$G$8:$G${term_end},Model!$D$8:$D${term_end},"{feature}",Model!$F$8:$F${term_end},{level}),0)')
            known.append(f'NOT({canonical})')
        put(qw,f'L{row}','='+'+'.join(cats))
        put(qw,f'AC{row}',f'=AND(LEN(A{row}&"")>0,COUNT(I{row}:K{row})=3,IFERROR(VALUE(C{row})>=0,LEN(C{row}&"")=0),IFERROR(VALUE(D{row})>=0,LEN(D{row}&"")=0),IFERROR(VALUE(H{row})>=0,LEN(H{row}&"")=0))')
        put(qw,f'M{row}',f'=IF(AC{row},{M("intercept")}+I{row}*{M("coefficient_CarAge")}+J{row}*{M("coefficient_DriverAge")}+K{row}*{M("coefficient_Density")}+L{row},"")')
        put(qw,f'N{row}',f'=IF(LEN(M{row}&"")=0,"",MAX(EXP(M{row}),0.000000001))')
        put(qw,f'O{row}',f'=IF(LEN(N{row}&"")=0,"",N{row}*{M("pooled_severity")}*{M("calibration_factor")})')
        guard=f'AND(AC{row},Controls!$B$31)'
        def fin(col,expr):put(qw,f'{col}{row}',f'=IF({guard},{expr},"")',fmt='0.00')
        fin('P',f'O{row}*{C(14)}*{C(15)}')
        fin('Q',f'MAX(0,MIN(1,(O{row}-{M("threshold_1")}+{C(10)}/2)/{C(10)}))');fin('R',f'MAX(0,MIN(1,(O{row}-{M("threshold_2")}+{C(10)}/2)/{C(10)}))')
        fin('S',f'IF({C(5)}="flat_constant",{C(6)},IF({C(5)}="tiered_constant",IF(O{row}<{M("threshold_1")},{C(7)},IF(O{row}<{M("threshold_2")},{C(8)},{C(9)})),{C(7)}+({C(8)}-{C(7)})*(3*Q{row}^2-2*Q{row}^3)+({C(9)}-{C(8)})*(3*R{row}^2-2*R{row}^3)))')
        fin('T',f'(P{row}+{C(11)})/(1-{C(12)}-{C(13)})');fin('U',f'(P{row}+{C(11)}+S{row})/(1-{C(12)}-{C(13)})');fin('V',f'MAX(U{row}+{C(16)},T{row})');fin('W',f'V{row}*(1-{C(12)}-{C(13)})-P{row}-{C(11)}');fin('X',f'--(U{row}+{C(16)}<T{row})')
        base=money(f'O{row}',M('margin_mode'),M('flat_risk_margin'),M('tier_money_1'),M('tier_money_2'),M('tier_money_3'),M('transition_width'))
        put(qw,f'Y{row}',f'=IF(AC{row},(O{row}*{M("trend_factor")}*{M("development_factor")}+{M("fixed_expense")}+{base})/(1-{M("variable_expense_ratio")}-{M("tax_ratio")}),"")')
        put(qw,f'Z{row}',f'=IF(AC{row},LN(O{row}/{M("median_quote_pure")}),"")')
        put(qw,f'AA{row}',f'=IF(OR({C(29)}="All",G{row}={C(29)}),1,0)')
        unusual=[f'C{row}>40',*known]
        for feature,col in rawcols.items():unusual.extend([f'IFERROR(VALUE({col}{row})<{M("minimum_"+feature)},FALSE)',f'IFERROR(VALUE({col}{row})>{M("maximum_"+feature)},FALSE)'])
        missing=','.join(f'LEN({c}{row}&"")=0' for c in ['B','C','D','E','F','G','H'])
        put(qw,f'AB{row}',f'=IF(NOT(AC{row}),"INVALID_INPUT",IF(OR({missing}),"MISSING_INPUT",IF(OR({",".join(unusual)}),"REVIEW","OK")))')
        put(qw,f'AD{row}',f'=IF(AC{row},IF(O{row}<{M("threshold_1")},1,IF(O{row}<{M("threshold_2")},2,3)),"")')
    for row in range(start,end+1):quote_formulas(row)
    for col in ['E','G']:qw.column_dimensions[col].width=29
    for col in ['I','J','K','L','M','Q','R','Y','Z','AA','AC','AD']:qw.column_dimensions[col].hidden=True
    qw.auto_filter.ref=f'A7:AD{end}';qw.freeze_panes='I8'
    qw.conditional_formatting.add(f'AB8:AB{end}',CellIsRule(operator='notEqual',formula=['"OK"'],fill=PatternFill('solid',fgColor=AMBER)))
    # One isolated new-risk calculator; excluded from portfolio summaries.
    calc=sheets['Calculator'];calc.column_dimensions['A'].width=28;calc.column_dimensions['B'].width=34
    for j,name in enumerate(q.columns,5):put(calc,f'A{j}',name);put(calc,f'B{j}',q.iloc[0][name].item() if hasattr(q.iloc[0][name],'item') else q.iloc[0][name],True)
    for j in range(8):put(qw,f'{L(j+1)}{calcrow}',f'=Calculator!B{j+5}')
    quote_formulas(calcrow)
    for row,label,col in [(16,'Frequency','N'),(17,'Pure expected loss','O'),(18,'Prospective expected loss','P'),(19,'Money allowance','S'),(20,'Technical premium','U'),(21,'Final quote','V'),(22,'Expected contribution','W'),(23,'Referral / validity','AB')]:put(calc,f'A{row}',label);put(calc,f'B{row}',f'=Quotes!{col}{calcrow}',fmt='0.0000' if row<23 else None)
    # Batch checks used as guards for live portfolio/scenario calculations.
    ch=sheets['Checks'];ch.column_dimensions['A'].width=47;ch.column_dimensions['B'].width=32
    put(ch,'A5','All 10,000 quotes calculated?');put(ch,'B5',f'=COUNT(Quotes!V8:V{end})={n}')
    put(ch,'A6','Portfolio / market calculations valid?');put(ch,'B6','=AND(B5,Controls!B31)')
    put(ch,'A7','Manual source-data referrals');put(ch,'B7',f'=COUNTIF(Quotes!AB8:AB{end},"REVIEW")+COUNTIF(Quotes!AB8:AB{end},"MISSING_INPUT")')
    put(ch,'A8','Minimum contribution under offered-risk basis');put(ch,'B8',f'=IF(B6,MIN(Quotes!W8:W{end}),"")')
    put(ch,'A10','Original input SHA256');put(ch,'B10',scalar['input_sha256'])
    # Live demand assumptions use frozen default technical reference, not current money rules.
    live=sheets['Market_Live'];head(live,7,['PolicyID','Include','Offered_quote','Competitor_reference','Demand_probability','Expected_bound_probability','Expected_premium','Expected_loss','Expected_expense','Expected_contribution'])
    put(live,'M5','Expected-volume acceptance');put(live,'N5',f'=IF(Checks!B6,MIN(1,{C(24)}/MAX(SUMPRODUCT(B8:B{end},E8:E{end}),0.000000000001)),"")')
    for row in range(start,end+1):
        put(live,f'A{row}',f'=Quotes!A{row}');put(live,f'B{row}',f'=Quotes!AA{row}')
        expr={'C':f'Quotes!V{row}','D':f'Quotes!Y{row}*{C(19)}*EXP({C(20)}*Quotes!Z{row})','E':f'1/(1+EXP(-LN({C(23)}/(1-{C(23)}))+{C(21)}*LN(C{row}/D{row})))','F':f'B{row}*E{row}*$N$5','G':f'C{row}*F{row}','H':f'Quotes!P{row}*{C(22)}*F{row}','I':f'(C{row}*({C(12)}+{C(13)})+{C(11)})*F{row}','J':f'G{row}-H{row}-I{row}'}
        for col,expression in expr.items():put(live,f'{col}{row}',f'=IF(Checks!$B$6,{expression},"")',fmt='0.0000')
    # All 216 scenarios calculate in Excel; the grid definitions are assumptions.
    saved=pd.read_csv(ROOT/'results/commercial_scenarios.csv');sw=sheets['Scenarios'];head(sw,7,['Competitor_multiplier','Risk_tilt','Elasticity','Bound_loss_multiplier','Money_action','Unconstrained_volume','Acceptance','Expected_volume','Expected_premium','Expected_loss','Expected_expense','Expected_contribution','Combined_ratio','Growth_met'])
    ranges={col:f'Quotes!${col}$8:${col}${end}' for col in ['AA','U','T','P','Y','Z']}
    for row,case in enumerate(saved.itertuples(index=False),8):
        for j,value in enumerate([case.competitor_multiplier,case.risk_tilt,case.elasticity,case.bound_loss_multiplier,case.money_adjustment],1):put(sw,f'{L(j)}{row}',value)
        offered=f'IF({ranges["U"]}+E{row}>{ranges["T"]},{ranges["U"]}+E{row},{ranges["T"]})'
        probability=f'(1/(1+EXP(-LN({C(23)}/(1-{C(23)}))+C{row}*LN(({offered})/({ranges["Y"]}*A{row}*EXP(B{row}*{ranges["Z"]}))))))'
        sx=lambda term:f'SUMPRODUCT({ranges["AA"]},{probability},{term})'
        expressions={'F':f'SUMPRODUCT({ranges["AA"]},{probability})','G':f'MIN(1,{C(24)}/MAX(F{row},0.000000000001))','H':f'F{row}*G{row}','I':f'G{row}*{sx(offered)}','J':f'G{row}*{sx(ranges["P"]+"*D"+str(row))}','K':f'G{row}*{sx("("+offered+")*("+C(12)+"+"+C(13)+")+"+C(11))}','L':f'I{row}-J{row}-K{row}','M':f'(J{row}+K{row})/MAX(I{row},0.000000000001)','N':f'H{row}>={C(25)}'}
        for col,expression in expressions.items():put(sw,f'{col}{row}',f'=IF(Checks!$B$6,{expression},"")',fmt='0.0000')
    sw.auto_filter.ref=f'A7:N{7+len(saved)}'
    rw=sheets['Robustness'];head(rw,7,['Money_action','Worst_contribution','Best_contribution','Minimum_volume','Growth_met','Profit_floor_met','Joint_feasible'])
    # Each action occupies every fourth row of the 216-row grid; use fixed 54-cell references.
    for row,action in enumerate(cfg['commercial']['quote_adjustments'],8):
        j=cfg['commercial']['quote_adjustments'].index(action);scenario_rows=list(range(8+j,8+len(saved),4));profits=','.join(f'Scenarios!L{k}' for k in scenario_rows);vols=','.join(f'Scenarios!H{k}' for k in scenario_rows)
        put(rw,f'A{row}',action)
        for col,expression in {'B':f'MIN({profits})','C':f'MAX({profits})','D':f'MIN({vols})','E':f'D{row}>={C(25)}','F':f'B{row}>={C(26)}','G':f'AND(E{row},F{row})'}.items():put(rw,f'{col}{row}',f'=IF(Checks!$B$6,{expression},"")')
    put(rw,'A15','Joint feasible action exists?');put(rw,'B15','=IF(Checks!B6,COUNTIF(G8:G11,TRUE)>0,"")')
    rw.merge_cells('A17:M18');put(rw,'A17','=IF(NOT(Checks!B6),"FIX INVALID INPUTS",IF(B15,"A listed action meets the joint constraints.","NO FEASIBLE ACTION: growth and worst expected contribution cannot both be satisfied in this scenario grid."))');rw['A17'].alignment=Alignment(wrap_text=True);rw['A17'].fill=PatternFill('solid',fgColor=AMBER)
    # Dashboard and stress summaries remain live under the region cohort control.
    dash=sheets['Dashboard'];dash.freeze_panes='A5'
    include=f'Quotes!$AA$8:$AA${end}';quotes=f'Quotes!$V$8:$V${end}';loss=f'Quotes!$P$8:$P${end}';contrib=f'Quotes!$W$8:$W${end}'
    summary_formulas={'B6':f'SUMPRODUCT({include},{quotes})/MAX(1,SUM({include}))','F6':f'SUMPRODUCT({include},{loss})/MAX(1,SUM({include}))','J6':f'SUMPRODUCT({include},{contrib})','N6':f'(SUMPRODUCT({include},{loss})+SUMPRODUCT({include},{quotes})*({C(12)}+{C(13)})+SUM({include})*{C(11)})/MAX(SUMPRODUCT({include},{quotes}),0.000000000001)'}
    for col,label in [('B','MEAN OFFERED QUOTE'),('F','EXPECTED POLICY LOSS'),('J','EXPECTED CONTRIBUTION'),('N','BASE COMBINED RATIO')]:
        idx=__import__('openpyxl').utils.column_index_from_string(col);dash.merge_cells(start_row=5,start_column=idx,end_row=5,end_column=idx+2);put(dash,col+'5',label);dash[col+'5'].font=Font(size=10,bold=True,color=GRAY)
        dash.merge_cells(start_row=6,start_column=idx,end_row=7,end_column=idx+2);put(dash,col+'6',f'=IF(Checks!B6,{summary_formulas[col+"6"]},"")',fmt='0.0%' if col=='N' else '#,##0.00');dash[col+'6'].font=Font(size=24,bold=True,color=TEAL);dash[col+'6'].fill=PatternFill('solid',fgColor=PALE)
    dash.merge_cells('A9:P10');put(dash,'A9','=IF(Checks!B6,"Live formulas | Source model is frozen | Expectations and assumed market behavior, not observed profit","FIX INVALID INPUTS: review Controls and Quotes before interpreting results.")');dash['A9'].alignment=Alignment(wrap_text=True)
    head(dash,12,['Risk_band','Risks','Mean_pure_loss','Mean_money','Mean_quote'])
    for row,band in enumerate([1,2,3],13):
        put(dash,f'A{row}',band);bands=f'Quotes!$AD$8:$AD${end}'
        for col,expr in {'B':f'COUNTIFS({include},1,{bands},A{row})','C':f'SUMIFS(Quotes!$O$8:$O${end},{include},1,{bands},A{row})/MAX(B{row},1)','D':f'SUMIFS(Quotes!$S$8:$S${end},{include},1,{bands},A{row})/MAX(B{row},1)','E':f'SUMIFS({quotes},{include},1,{bands},A{row})/MAX(B{row},1)'}.items():put(dash,f'{col}{row}',f'=IF(Checks!B6,{expr},"")',fmt='0' if col=='B' else '0.00')
    for row,label,expr in [(19,'Expected bound policies',f'SUM(Market_Live!F8:F{end})'),(20,'Expected bound contribution',f'SUM(Market_Live!J8:J{end})'),(21,'Selected combined ratio',f'(SUM(Market_Live!H8:H{end})+SUM(Market_Live!I8:I{end}))/MAX(SUM(Market_Live!G8:G{end}),0.000000000001)'),(22,'Source-data referrals','Checks!B7'),(23,'Region cohort','Controls!B29'),(25,'Joint robust feasibility','IF(Robustness!B15,"YES","NO")')]:put(dash,f'A{row}',label);put(dash,f'D{row}',f'=IF(Checks!B6,{expr},"")',fmt='0.0%' if row==21 else 'General' if row in [23,25] else '#,##0.00')
    chart=BarChart();chart.title='Mean quote by locked risk band';chart.add_data(Reference(dash,min_col=5,min_row=12,max_row=15),titles_from_data=True);chart.set_categories(Reference(dash,min_col=1,min_row=13,max_row=15));chart.width=19;chart.height=8;dash.add_chart(chart,'H12')
    dash.merge_cells('A28:P30');put(dash,'A28','Development-only research. The original historical test remains excluded. Scenario constraints concern expected outcomes in a finite grid. Fixed money is not automatically fair or capital adequate.');dash['A28'].alignment=Alignment(wrap_text=True,vertical='top');dash.print_area='A1:P30'
    st=sheets['Stress'];head(st,7,['Frequency_factor','Severity_factor','Combined_ratio','Expected_contribution','Additional_net_money_per_policy'])
    i=8
    for f in [.8,1,1.1,1.2,1.5]:
        for s in [.8,1,1.1,1.25,1.5]:
            put(st,f'A{i}',f);put(st,f'B{i}',s);cost=f'SUMPRODUCT({include},{loss})*A{i}*B{i}+SUMPRODUCT({include},{quotes})*({C(12)}+{C(13)})+SUM({include})*{C(11)}';premium=f'SUMPRODUCT({include},{quotes})'
            for col,expr in {'C':f'({cost})/MAX({premium},0.000000000001)','D':f'{premium}-({cost})','E':f'MAX(0,({cost})-({premium}))/MAX(1,SUM({include}))'}.items():put(st,f'{col}{i}',f'=IF(Checks!B6,{expr},"")');i+=0
            i+=1
    curves=sheets['Margin_Curves'];head(curves,7,['Pure_loss','Flat_quote','Strict_tier_quote','Smooth_shoulder_quote'])
    for row,pure in enumerate(np.linspace(max(0,summary['thresholds'][0]-35),summary['thresholds'][1]+40,151),8):
        put(curves,f'A{row}',float(pure))
        for col,mode in [('B','flat_constant'),('C','tiered_constant'),('D','smooth_tiered_constant')]:
            m=money(f'A{row}',f'"{mode}"',C(6),C(7),C(8),C(9),C(10));tech=f'(A{row}*{C(14)}*{C(15)}+{C(11)}+{m})/(1-{C(12)}-{C(13)})';floor=f'(A{row}*{C(14)}*{C(15)}+{C(11)})/(1-{C(12)}-{C(13)})';put(curves,f'{col}{row}',f'=IF(Controls!$B$31,MAX({tech}+{C(16)},{floor}),"")')
    lc=LineChart();lc.title='Money-rule boundary behavior';lc.add_data(Reference(curves,min_col=2,max_col=4,min_row=7,max_row=158),titles_from_data=True);lc.set_categories(Reference(curves,min_col=1,min_row=8,max_row=158));lc.width=23;lc.height=11;curves.add_chart(lc,'G8')
    imported('Python_CV',pd.read_csv(ROOT/'results/cv_model_summary.csv'))
    native=R/'results/native_r_cv_model_summary.csv'
    if native.exists():imported('R_Native_CV',pd.read_csv(native))
    else:put(sheets['R_Native_CV'],'A7','Native R research results are supplied separately; run r_version/run.R --research.')
    imported('Risk_Budget',pd.read_csv(ROOT/'results/risk_budget_scenarios.csv'))
    imported('Credibility',pd.read_csv(ROOT/'results/segment_credibility.csv'))
    read=sheets['Read_Me'];read.column_dimensions['A'].width=29
    notes=[('Purpose','Frozen V2 model scoring, editable monetary pricing, 216 live scenarios and joint volume/contribution feasibility.'),('Model','Exact selected GLM frequency x pooled severity. Numeric standardization, category coefficients and calibration are frozen in Model.'),('Training scope','Excel does not train GLM/GAM/GBM models. Python comparison results and separately labeled native R comparison results are evidence tabs.'),('How to use','Edit blue cells in Controls. Use Calculator for a new risk. Quotes contains the 10,000 source quote risks; model formulas remain inspectable.'),('Cohort','Controls B29 selects the dashboard and market cohort. The table autofilter does not redefine dashboard totals.'),('Money units','Allowances are net contribution targets. Gross quote effect is money / (1 - variable expense - tax).'),('Scenarios','Competitor, elasticity, adverse selection and capacity are assumptions. No actual market profit, causal demand or realized capacity guarantee.'),('Capital evidence','Risk_Budget and Credibility are frozen saved research evidence; price controls do not rerun capital simulation or statistical model fitting.'),('Evidence boundary','Development-only research after prior exploration. Historical test data excluded from R inputs. No production or regulatory approval.'),('Validation','See cross_format verification files. Independent offline spreadsheet formula evaluation is used; desktop Excel automation is blocked by the sandbox.'),('Compatibility','Macro-free .xlsx; standard IF/SUMIFS/SUMPRODUCT/EXP/LN formulas. No Python/R connection, external link, add-in or server is required to quote.'),('Recalculate','Automatic calculation and full calculation on open are enabled. Cached formula results are supplied after independent formula-engine validation.'),('Model identity',scalar['input_sha256']),('Language','All deliverable content is English. Original Python V2 scientific artifacts are preserved.'),('Data handling','This workbook embeds source-derived quote risk factors. Review sharing conditions before publishing it.')]
    for row,(label,text) in enumerate(notes,6):put(read,f'A{row}',label);read.merge_cells(start_row=row,start_column=2,end_row=row,end_column=16);put(read,f'B{row}',text);read[f'B{row}'].alignment=Alignment(wrap_text=True,vertical='top');read.row_dimensions[row].height=38
    for ws in wb:
        ws.sheet_properties.pageSetUpPr.fitToPage=True;ws.print_options.horizontalCentered=True
        ws.sheet_properties.tabColor=TEAL if ws.title in ['Dashboard','Controls','Calculator'] else '8FAAB4'
    wb.calculation=CalcProperties(calcId=191029,fullCalcOnLoad=True,forceFullCalc=True,calcMode='auto')
    destination=OUT/'CAS_Motor_Pricing_V2.xlsx';wb.save(destination)
    (WORK/'excel_formula_map.json').write_text(json.dumps({'workbook':str(destination),'formula_cells':formula_cells,'quote_start':start,'quote_end':end,'calculator_row':calcrow,'model_scalars':smap}),encoding='utf-8')
    print(json.dumps({'workbook':str(destination),'formulas':sum(map(len,formula_cells.values())),'sheets':len(names)}))

if __name__=='__main__':build()
