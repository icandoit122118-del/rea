"""Bounded real-source sample audit. No PF profile promotion and no old output writes.

Selection JSON: [{"expected_bch_name":"pm0004_00","coverage":"early stage",
"model":[36,0],"textures":[[38,0]],"motions":[[41,0],[42,0]]}].
The illustrated indices are examples, never species identity evidence. Actual BCH
model names must match the explicit expected name before export is permitted.
Without --selection, only source preflight runs; model samples remain untested.
"""
from __future__ import annotations
import argparse,datetime,hashlib,importlib.util,json,pathlib,subprocess,traceback

ROOT=pathlib.Path(__file__).resolve().parents[1]
def load_script(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
garc=load_script('oras_garc_select')
SAMPLE_INTENT=[('pm0004_00','early small biped'),('pm0006_00','winged articulated body'),('pm0007_00','shell body'),('pm0025_00','long ears and tail'),('pm0130_00','long segmented body'),('pm0143_00','large body'),('pm0376_00','four articulated limbs'),('pm0384_00','long body and multiple fins')]
HISTORICAL_ROM_SHA='c0dd8cd9e06604171b91c9413c830715255b02f0339120b31dbb54eb91ae222d'
def save(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
def run(command,output,timeout=180):
    p=subprocess.run(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    raw={'command':command,'exit_code':p.returncode,'stdout':p.stdout.decode('utf-8','replace'),'stderr':p.stderr.decode('utf-8','replace')};save(output,raw)
    return p.returncode,json.loads(raw['stdout'])
def validate_selection(items):
    if not isinstance(items,list) or not 1<=len(items)<=9:raise ValueError('selection must contain 1..9 real samples')
    names=[]
    for item in items:
        if not isinstance(item,dict):raise ValueError('sample must be an object')
        name=item.get('expected_bch_name')
        if not isinstance(name,str) or len(name)!=9 or not name.startswith('pm') or not name[2:6].isdigit() or name[6]!='_' or not name[7:].isdigit():raise ValueError('expected exact pm####_## BCH name')
        names.append(name)
        pairs=[item.get('model')]+item.get('textures',[])+item.get('motions',[])
        if not 1<=len(pairs)<=8:raise ValueError('each sample requires 1..8 explicit members')
        for pair in pairs:
            if not isinstance(pair,list) or len(pair)!=2 or any(type(v) is not int for v in pair) or not 0<=pair[0]<10000 or not 0<=pair[1]<32:raise ValueError('invalid explicit member pair')
        if len({tuple(p) for p in pairs})!=len(pairs):raise ValueError('duplicate member selection')
    if len(set(names))!=len(names):raise ValueError('duplicate expected model name')
    return items
def main():
    pa=argparse.ArgumentParser(description=__doc__);pa.add_argument('--source',required=True);pa.add_argument('--output',required=True);pa.add_argument('--selection');ns=pa.parse_args()
    out=pathlib.Path(ns.output);assert out.is_absolute();out.mkdir(parents=True,exist_ok=False)
    selection=validate_selection(json.loads(pathlib.Path(ns.selection).read_text(encoding='utf-8'))) if ns.selection else []
    report={'schema':'rea3d-generality-real-source-validation-v1','measured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'execution':'native_python_parser_and_node_CLI','native_MCP_call_claimed':False,'source':ns.source,'source_full_sha256_historical_only':HISTORICAL_ROM_SHA,'source_full_sha256_rechecked':False,'sample_goal':8,'all_species_coverage':False,'planned_sample_intent':[{'expected_model':n,'coverage_intent':r,'identity_verified':False,'status':'not_tested'} for n,r in SAMPLE_INTENT],'samples':[],'failures':[],'exports_attempted':0,'exports_passed_gltf_validation':0,'game_executed':False,'original_rendering_parity_verified':False,'facial_profile_scope':'PF remains the exact identified Bulbasaur-only profile; other PF is never enabled here','gf1_native_scope':'only the two existing identified GF1 hashes; unknown inputs retain reference assumptions'}
    inv=None;table=None
    try:
        inv=garc.romfs.inspect_romfs(ns.source);save(out/'romfs-inventory.json',inv)
        report['romfs_metadata_read']=True;report['source_metadata_identity']=inv['source'];report['romfs_metadata_sha256']=inv['metadata_sha256']
        table=garc.inspect_archive(ns.source,str(out/'romfs-inventory.json'),'a/0/0/8',prefix_count=0);save(out/'garc-inventory.json',table)
        report['garc_table_read']=True;report['garc_table_sha256']=table['table_sha256']
    except Exception as exc:
        report['failures'].append({'stage':'source_GARC_table_read','exception':type(exc).__name__,'errno':getattr(exc,'errno',None),'winerror':getattr(exc,'winerror',None),'message':str(exc),'traceback':traceback.format_exc()})
        report['garc_table_read']=False
        for sample in report['planned_sample_intent']:sample['status']='not_extracted_source_read_failure'
    if table is not None and selection:
        from rea3d_core.bridge import read_assets,describe
        for item in selection:
            name=item['expected_bch_name'];entry={'expected_model':name,'coverage':item.get('coverage'),'status':'started','identity_verified':False};report['samples'].append(entry)
            try:
                directory=out/name;directory.mkdir();pairs=[item['model']]+item.get('textures',[])+item.get('motions',[])
                manifest=garc.extract_members(ns.source,str(out/'romfs-inventory.json'),'a/0/0/8',pairs,str(directory/'members'));save(directory/'selection-source.json',item)
                paths={(e['index'],e['subindex']):e['output_path'] for e in manifest['entries']};primary=paths[tuple(item['model'])]
                assets=read_assets(primary);description=describe(assets);save(directory/'model-inspection.json',description)
                matches=[i for i,m in enumerate(assets.models) if m.name==name]
                if len(matches)!=1:raise ValueError(f'BCH name mismatch; actual names: {[m.name for m in assets.models]}')
                entry.update(identity_verified=True,actual_bch_name=assets.models[matches[0]].name,source_members=manifest['entries'])
                args={'path':primary,'output_dir':str(directory/'export'),'model_index':matches[0],'texture_paths':[paths[tuple(p)] for p in item.get('textures',[])],'motion_paths':[paths[tuple(p)] for p in item.get('motions',[])],'fps':30}
                save(directory/'export-request.json',{'version':1,'method':'export','args':args});report['exports_attempted']+=1
                code,response=run(['node',str(ROOT/'src/cli.mjs'),'--file',str(directory/'export-request.json')],directory/'export-CLI-operation.json')
                if code or not response.get('ok'):raise ValueError(f'export failed: {response.get("error")}')
                result=response['result'];entry['export']=result
                code,validation=run(['node',str(ROOT/'scripts/verify-asset-glb.mjs'),result['glb_path'],str(directory/'gltf-validation.json')],directory/'gltf-validation-command.json')
                entry['gltf_validation_summary']=validation
                if code:raise ValueError('glTF validator returned errors')
                entry['status']='exported_gltf_validated_partial_fidelity';report['exports_passed_gltf_validation']+=1
                entry['model_name_verified_from_BCH']=True
            except Exception as exc:
                entry.update(status='failed',failure={'exception':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
    report['status']='blocked_source_asset_read' if not table else ('preflight_only_no_selection' if not selection else 'selected_samples_partial_validation')
    report['selected_species_count']=sum(s.get('identity_verified',False) for s in report['samples'])
    save(out/'generality-report.json',report)
    save(out/'SHA256SUMS.json',{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps({'output':str(out),'status':report['status'],'selected_species_count':report['selected_species_count'],'exports_attempted':report['exports_attempted'],'exports_passed_gltf_validation':report['exports_passed_gltf_validation'],'failures':[{k:v for k,v in e.items() if k!='traceback'} for e in report['failures']]},ensure_ascii=True))
    return 0 if table is not None else 2
if __name__=='__main__':raise SystemExit(main())
