"""Evaluate actual XLSX formulas offline and compare independent Python/R outputs."""
import json,sys,zipfile,re,hashlib,xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np,pandas as pd,openpyxl,joblib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from excel_formula_engine import Engine,FormulaError
from format_paths import WORK
from pricing import quote_prices

def verify():
    path=ROOT/'excel_version/CAS_Motor_Pricing_V2.xlsx';wb=openpyxl.load_workbook(path)
    engine=Engine(wb);source=pd.read_csv(ROOT/'results/quote_detail.csv');meta=json.loads((WORK/'excel_formula_map.json').read_text())
    end=meta['quote_end'];calcrow=meta['calculator_row'];errors={}
    mapping={'N':'frequency_uncalibrated','O':'pure_premium','P':'expected_loss','S':'money_margin','T':'cost_floor','U':'technical_premium','V':'quote','W':'expected_contribution'}
    print('Evaluating all 10,000 selected-model quotes from XLSX formulas...',flush=True)
    for col,key in mapping.items():
        actual=np.array([engine.cell('Quotes',f'{col}{r}') for r in range(8,end+1)],dtype=float)
        errors[key]=float(np.max(np.abs(actual-source[key].to_numpy())))
        np.testing.assert_allclose(actual,source[key],rtol=1e-11,atol=1e-9)
    flags=[engine.cell('Quotes',f'AB{r}') for r in range(8,end+1)]
    np.testing.assert_array_equal(np.array(flags)!='OK',source.manual_review)
    print('Quote parity passed; evaluating 216 complete Excel scenario formulas...',flush=True)
    scenarios=pd.read_csv(ROOT/'results/commercial_scenarios.csv')
    scenario_map={'F':'unconstrained_expected_policies','G':'random_acceptance_ratio','H':'expected_policies','I':'expected_premium','J':'expected_loss','K':'expected_expenses','L':'expected_contribution','M':'combined_ratio'}
    for col,key in scenario_map.items():
        actual=np.array([engine.cell('Scenarios',f'{col}{r}') for r in range(8,224)],dtype=float)
        errors['scenario_'+key]=float(np.max(np.abs(actual-scenarios[key].to_numpy())))
        np.testing.assert_allclose(actual,scenarios[key],rtol=1e-9,atol=1e-7)
    robust=pd.read_csv(ROOT/'results/commercial_robust_comparison.csv')
    for col,key in {'B':'worst_contribution','C':'best_contribution','D':'minimum_expected_policies','E':'all_growth_targets_met','F':'profit_floor_met','G':'joint_constraints_met'}.items():
        actual=np.array([engine.cell('Robustness',f'{col}{r}') for r in range(8,12)])
        if robust[key].dtype==bool:np.testing.assert_array_equal(actual,robust[key])
        else:np.testing.assert_allclose(actual.astype(float),robust[key],rtol=1e-9,atol=1e-7)
    assert engine.cell('Robustness','B15') is False
    # Evaluate every written formula, including all helper cells and dashboard checks.
    for sheet,addresses in meta['formula_cells'].items():
        for address in addresses:
            value=engine.cell(sheet,address)
            if isinstance(value,float) and not np.isfinite(value):raise AssertionError(f'Nonfinite {sheet}!{address}')
    print('All formula cells evaluated. Writing caches for immediate offline viewing...',flush=True)
    cache={sheet:{address:engine.cell(sheet,address) for address in addresses} for sheet,addresses in meta['formula_cells'].items()}
    # Snapshot before counterfactuals; only the delivered default state is cached.
    model=joblib.load(ROOT/'results/pricing_engine.joblib');risk=pd.read_csv(ROOT/'r_version/data/quotes.csv').iloc[[0]].copy()
    edge_cases=[{'CarAge':0},{'Brand':'FIAT'},{'Power':'UNSEEN_POWER'},{'CarAge':None},{'Density':0}]
    case_errors=[]
    for changes in edge_cases:
        for j,key in enumerate(risk.columns,5):engine.set('Calculator',f'B{j}',risk.iloc[0][key].item() if hasattr(risk.iloc[0][key],'item') else risk.iloc[0][key])
        changed=risk.copy()
        for key,value in changes.items():
            engine.set('Calculator',f'B{5+list(risk.columns).index(key)}',value);changed[key]=value
        expected=model.quote(changed)
        actual=float(engine.cell('Quotes',f'V{calcrow}'));error=abs(actual-expected.quote.iloc[0]);case_errors.append(error)
        assert error<1e-8,(changes,error)
        assert (engine.cell('Quotes',f'AB{calcrow}')!='OK')==bool(expected.manual_review.iloc[0])
    engine.set('Controls','B12',1)
    assert not engine.cell('Controls','B31')
    assert engine.cell('Quotes',f'V{calcrow}')==''
    # Financial policy counterfactuals are checked on the isolated calculator.
    config=json.loads((ROOT/'config.json').read_text());financial=0
    for j,key in enumerate(risk.columns,5):engine.set('Calculator',f'B{j}',risk.iloc[0][key].item() if hasattr(risk.iloc[0][key],'item') else risk.iloc[0][key])
    engine.set('Controls','B12',.15)
    for mode in ['flat_constant','tiered_constant','smooth_tiered_constant']:
        for adjustment in [-1000,0,15]:
            engine.set('Controls','B5',mode);engine.set('Controls','B16',adjustment)
            p=config['pricing'].copy();p.update(margin_mode=mode,commercial_adjustment=adjustment)
            expected=quote_prices(model.quote(risk).pure_premium,p,model.thresholds).quote.iloc[0]
            actual=float(engine.cell('Quotes',f'V{calcrow}'));assert abs(actual-expected)<1e-8
            financial+=1
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    namespace=ns['x'];ET.register_namespace('',namespace)
    # Fill standard <v> formula caches without replacing any formula or chart.
    with zipfile.ZipFile(path) as z:
        contents={name:z.read(name) for name in z.namelist()}
    for i,ws in enumerate(wb.worksheets,1):
        name=f'xl/worksheets/sheet{i}.xml'
        if not meta['formula_cells'][ws.title]:continue
        tree=ET.fromstring(contents[name])
        for cell in tree.findall('.//x:sheetData/x:row/x:c',ns):
            address=cell.get('r')
            if address not in cache[ws.title]:continue
            value=cache[ws.title][address]
            v=cell.find('x:v',ns)
            if v is None:v=ET.SubElement(cell,f'{{{namespace}}}v')
            if isinstance(value,(bool,np.bool_)):cell.set('t','b');v.text='1' if value else '0'
            elif isinstance(value,(int,float,np.integer,np.floating)):cell.pop('t',None) if hasattr(cell,'pop') else cell.attrib.pop('t',None);v.text=repr(float(value))
            else:cell.set('t','str');v.text=str(value)
        contents[name]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,data in contents.items():z.writestr(name,data)
    checked=openpyxl.load_workbook(path,data_only=True,read_only=True)
    reopened=[row[0] for row in checked['Quotes'].iter_rows(min_row=8,max_row=end,min_col=22,max_col=22,values_only=True)]
    np.testing.assert_allclose(reopened,source.quote,rtol=1e-11,atol=1e-9)
    assert checked['Robustness']['B15'].value is False
    result={'workbook':path.name,'formula_count':sum(map(len,meta['formula_cells'].values())),
        'formula_evaluation':'Actual workbook formulas parsed to ASTs; scalar and NumPy array semantics. This is not Microsoft Excel.',
        'all_10000_quotes_checked':True,'all_216_scenarios_checked':True,'joint_constraint_infeasibility_preserved':True,
        'referral_flags_match_python':True,'counterfactual_risk_cases':len(edge_cases),'counterfactual_money_cases':financial,
        'invalid_parameters_block_prices':True,'cached_values_written_and_reopened':True,
        'maximum_absolute_errors':errors,'maximum_counterfactual_quote_error':max(case_errors),
        'native_excel_automation':'Unavailable: sandbox COM activation returned E_ACCESSDENIED; no native Excel recalculation is claimed.',
        'macros':False,'external_workbook_links':False,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (ROOT/'excel_version/EXCEL_VERIFICATION.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    return result

if __name__=='__main__':verify()
