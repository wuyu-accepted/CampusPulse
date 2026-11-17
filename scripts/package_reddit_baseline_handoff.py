"""Package processed CMV inputs in the campus forum CSV and Agent contracts.

Reads only frozen local artifacts; creates no language and calls no Provider.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from campus_pulse.package_forum_twin_v1 import _zip_tree, _file_hashes
from audit_reddit_cmv_pilot import paper_cases
from backend.campus_pulse.reddit_cmv_execution import CmvRunRepository
from envs.campus_pulse_governance.reddit.particle_model import save_particle_snapshot, load_particle_snapshot, STATE_FIELDS

BASE=ROOT/'data/private/reddit/cmv/lightweight-v3'
RUN=ROOT/'data/private/reddit/cmv/runs/cmv-run-da2377c860a41aa41605'
EVENT=ROOT/'data/private/reddit/cmv/event-overlays/healthcare-education-v2'


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def alias(kind,value):
    return None if value is None else kind+'-'+hashlib.sha256(('cmv-team-export:'+kind+':'+str(value)).encode()).hexdigest()[:20]


def clean_text(value):
    text=str(value or '')
    text=re.sub(r'https?://\S+','[link]',text)
    text=re.sub(r'(?<!\w)/?u/[A-Za-z0-9_-]+','[user]',text)
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[email]',text)
    return text


def sanitized(value):
    if isinstance(value,dict):
        return {k:sanitized(v) for k,v in value.items()
                if not k.endswith('_private') and k not in {'author_key','source_id','parent_source_id','api_key','apiKey','authorization','endpoint','proxy'}
                and not k.endswith('_sha256') and k not in {'world_sha256','profile_prompt_sha256'}}
    if isinstance(value,list): return [sanitized(v) for v in value]
    return value


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def write_lines(path,values):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,'wt',encoding='utf-8') as stream:
        for row in values: stream.write(json.dumps(row,ensure_ascii=False)+'\n')


def gzip_rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            if line.strip(): yield json.loads(line)


def corpus_export(out):
    counts={}
    background=[]
    for name,source in [('background','background.private.jsonl.gz'),('candidate-histories','candidate-histories.private.jsonl.gz')]:
        rows=[]; seen=set()
        for r in gzip_rows(BASE/source):
            identity=(r['source_id_private'],r['content_sha256'])
            if identity in seen: continue
            seen.add(identity)
            thread=alias('thread',r['conversation_key_private'])
            message=alias('message',r['source_id_private'])
            parent=alias('message',r.get('parent_source_id_private'))
            is_root=str(r['source_id_private'])==str(r['conversation_key_private']) or parent is None
            rows.append({'post_code':thread,'message_id':message,'comment_id':None if is_root else message,
                         'parent_message_id':parent,'author_id':alias('author',r['author_key_private']),
                         'title':clean_text(r.get('title_private','')),'content':clean_text(r['text']),
                         'timestamp_utc':r['timestamp'],'real_date':datetime.fromtimestamp(r['timestamp'],timezone.utc).isoformat(),
                         'message_type':'post' if is_root else 'comment','sampling_stratum':r['sampling_stratum'],
                         'same_text_elsewhere':bool(r.get('exact_text_duplicate'))})
        rows.sort(key=lambda r:(r['post_code'],r['timestamp_utc'],r['message_id']))
        for i,row in enumerate(rows): row['sequence']=i
        write_lines(out/'corpus'/f'{name}.jsonl.gz',rows)
        counts[name]=len(rows)
        if name=='background': background=rows
    # CSV required fields match the actual campus corpus reader. No missing root is invented.
    threads={}
    for row in background: threads.setdefault(row['post_code'],[]).append(row)
    post_fields=['post_code','real_date','content','author_id','title','message_id']
    comment_fields=['post_code','comment_id','real_date','floor','content','author_id','parent_message_id','actual_timestamp_utc']
    post_rows=[];comment_rows=[]; manifest=[]
    for code,rows in sorted(threads.items()):
        roots=[r for r in rows if r['message_type']=='post']
        parent_date=roots[0]['real_date'] if roots else rows[0]['real_date']
        manifest.append({'post_code':code,'root_available':bool(roots),'sampled_rows':len(rows),
                         'date_source':'root_timestamp' if roots else 'earliest_retained_timestamp',
                         'title':rows[0]['title']})
        for row in roots: post_rows.append({k:row[k] for k in post_fields})
        for floor,row in enumerate((r for r in rows if r['message_type']=='comment'),1):
            comment_rows.append({'post_code':code,'comment_id':row['comment_id'],'real_date':parent_date,
                                 'floor':floor,'content':row['content'],'author_id':row['author_id'],
                                 'parent_message_id':row['parent_message_id'],'actual_timestamp_utc':row['timestamp_utc']})
    for name,fields,rows in [('posts.csv',post_fields,post_rows),('comments.csv',comment_fields,comment_rows)]:
        with (out/'corpus'/name).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    write_json(out/'corpus/thread-manifest.json',manifest)
    candidates=[]
    for r in gzip_rows(BASE/'candidate-authors.private.jsonl.gz'):
        candidates.append({**sanitized(r),'author_id':alias('author',r['author_key_private'])})
    write_json(out/'corpus/candidate-authors.json',candidates)
    write_lines(out/'corpus/candidate-clusters.jsonl.gz',
                [sanitized(r) for r in gzip_rows(BASE/'candidate-clusters.private.jsonl.gz')])
    write_json(out/'corpus/clustering-release.json',sanitized(read(BASE/'candidate-clustering-release.private.json')))
    selected=read(BASE/'world-candidates.private.json')
    write_json(out/'world/agent-history-bindings.json',[
        {'agent_id':r['agent_id'],'author_id':alias('author',r['author_key']),
         'history_count':r['history_count'],'thread_count':r['thread_count'],'span_days':r['span_days']}
        for r in selected])
    counts.update({'candidate_authors':len(candidates),'posts_csv':len(post_rows),'comments_csv':len(comment_rows),
                   'background_threads':len(threads),'threads_without_retained_root':sum(not t['root_available'] for t in manifest)})
    return counts


def event_export(out):
    episode=read(EVENT/'development-event.private.json')['episode']
    seed=sanitized(episode)
    for r in seed['historical_seed_messages']: r['text']=clean_text(r['text'])
    write_json(out/'events/main-event.json',seed)
    root=seed['historical_seed_messages'][0]
    write_json(out/'events/scenario.json',{'scenario_id':episode['episode_id'],'title':seed['title'],
        'initial_public_post':{'post_code':episode['episode_id'],'content':root['text'],'created_tick':0},
        'observed_prefix':seed['historical_seed_messages'],'input_cutoff_timestamp':seed['input_cutoff_timestamp'],
        'branches':['natural','D'],'ticks':list(range(7)),'held_out_future_in_generation':False})
    # Preserve the other frozen event inputs as optional materials, not extra completed runs.
    write_json(out/'events/other-frozen-inputs.json',sanitized(read(BASE/'episode-observations.private.json')))
    targets=read(EVENT/'evaluation-only.private.json')
    future=[{'message_id':alias('message',r['source_id_private']),
             'parent_message_id':alias('message',r['parent_source_id_private']),
             'author_id':alias('author',r['author_key_private']),'timestamp_utc':r['timestamp'],
             'content':clean_text(r['text_private'])} for r in targets['targets']]
    write_lines(out/'evaluation-only/observed-continuation.jsonl.gz',future)
    other=[]
    for event in gzip_rows(BASE/'episode-evaluation-only.private.jsonl.gz'):
        other.append({'episode_id':event['episode_id'],'targets':[
            {'message_id':alias('message',r['source_id_private']),'parent_message_id':alias('message',r['parent_source_id_private']),
             'author_id':alias('author',r['author_key_private']),'timestamp_utc':r['timestamp'],
             'content':clean_text(r['text_private'])} for r in event['targets']]})
    write_lines(out/'evaluation-only/other-event-continuations.jsonl.gz',other)
    return len(future)


def runtime_export(out,world):
    plan=read(RUN/'run-plan.private.json')
    journal=[json.loads(line) for line in (RUN/'runtime-journal.private.jsonl').read_text('utf-8').splitlines()]
    records={}
    for row in journal:
        if row.get('artifact_type')=='resident_input_batch':
            for r in row['artifact']['records']: records[r['prompt_sha256']]=(row['branch'],row['tick'],r)
    inputs=[];outputs=[]
    with sqlite3.connect((RUN/'provider-ledger.private.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as conn:
        cached=conn.execute("SELECT prompt_sha256,validated_decision_json FROM v7_live_cache WHERE slot_kind='resident'").fetchall()
    for sha,raw in sorted(cached):
        branch,tick,r=records[sha]
        case_id=hashlib.sha256(sha.encode()).hexdigest()[:20]
        identity={'case_id':case_id,'agent_id':r['agent_id'],'branch':branch,'tick':tick}
        inputs.append({**identity,'input':json.loads(r['agent_prompt']),
                       'previous_tick_state':sanitized(r.get('previous_tick_state')),
                       'decision_input_state':sanitized(r.get('decision_input_state'))})
        outputs.append({**identity,'method':'CampusPulse','output':json.loads(raw)})
    write_lines(out/'inputs/paired-observations.jsonl.gz',inputs)
    write_lines(out/'evaluation-only/ours-decisions.jsonl.gz',outputs)
    write_json(out/'inputs/activation-plan.json',{
        'episode_id':plan['episode_id'],'shared_tick':1,'resident_slots':sanitized(plan['resident_slots']),
        'governance_slots':sanitized(plan['governance_slots']),'timeline':plan['timeline'],
        'original_live_model':plan['expected_model'],'original_live_particle_observation_model':plan.get('particle_observation_model','legacy'),
        'same_tick_outputs_visible_only_after_commit':True})
    repo=CmvRunRepository(RUN,run_id='cmv-run-'+plan['plan_sha256'][:20],plan_sha256=plan['plan_sha256'],world_sha256=world['world_sha256'])
    seen=set();state_rows=[];particle_rows=[]
    for e in repo._read_events():
        if e['event_type']!='tick_checkpoint_committed': continue
        identity=(e['branch'],e['tick'])
        if identity in seen: continue
        seen.add(identity)
        repo.latest_checkpoint_event=lambda branch, checkpoint_label=None,selected=e:selected
        state=repo.restore_latest(e['branch']);snap=state.particles
        save_particle_snapshot(snap,out/'evaluation-only/particles'/f'{e["branch"]}-tick-{e["tick"]}.npz')
        if e['branch']=='shared_baseline' and e['tick']==0:
            # Match the actual event-bound prior, not the generic world prior.
            save_particle_snapshot(snap,out/'world/initial-particles.npz')
        means=(snap.values*snap.conditional_weights[:,:,None]).sum(axis=1)
        for i,aid in enumerate(snap.agent_ids):
            state_rows.append({'condition':'full','method':'CampusPulse','branch':e['branch'],'tick':e['tick'],
                               'agent_id':aid,**{field:float(means[i,k]) for k,field in enumerate(STATE_FIELDS)}})
            for j in range(10):
                particle_rows.append({'agent_id':aid,'branch':e['branch'],'tick':e['tick'],'particle_index':j,
                    'conditional_weight':float(snap.conditional_weights[i,j]),'design_weight':float(snap.design_weights[i]),
                    **{field:float(snap.values[i,j,k]) for k,field in enumerate(STATE_FIELDS)}})
    write_lines(out/'evaluation-only/combined_population_states.jsonl.gz',state_rows)
    (out/'evaluation-only/combined_population_states.jsonl').write_text(
        ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in state_rows),encoding='utf-8')
    write_lines(out/'evaluation-only/full-particles.jsonl.gz',particle_rows)
    for name in ('paper-metrics.private.json','paper-blind-review.private.json','paper-data-panels.private.md','paper-data-panels.private.json',
                 'offline-particle-panel.private.json','offline-feature-panel.private.json'):
        # Review file contains its old preflight and usage: retain labels only, not transport materials.
        if name=='paper-blind-review.private.json':
            write_json(out/'evaluation-only/blind-review-labels.json',read(RUN/name)['reviews'])
        elif name.endswith('.md'):
            shutil.copyfile(RUN/name,out/'evaluation-only/data-panels.md')
        else:
            write_json(out/'evaluation-only'/name.replace('.private',''),sanitized(read(RUN/name)))
    write_json(out/'inputs/output-template.json',{
        **{k:inputs[0][k] for k in ('case_id','agent_id','branch','tick')},'method':'YOUR_BASELINE',
        'action':'read','visible_text':'','stance':'uncertain','emotion':'neutral',
        'parent_message_id':None,'quote_message_id':None,'pre_state':None,'post_state':None,
        'memory_update':'','is_template':True})
    return {'paired_observations':len(inputs),'parent_state_rows':len(state_rows),'particle_rows':len(particle_rows),'checkpoints':len(seen)}


def latest_natural_export(out, world):
    """Export the new 196-slot run, refusing partial or old-version results."""
    from run_reddit_cmv_natural_panel import OUT as natural_dir, export_completed
    pf=read(natural_dir/'preflight.private.json')
    summary=export_completed(pf)
    if pf['plan']['world_sha256'] != world['world_sha256']:
        raise ValueError('latest Natural world differs from corpus world')
    handoff=natural_dir/'handoff'
    for path in handoff.rglob('*'):
        if path.is_file():
            target=out/path.relative_to(handoff)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,target)
    shutil.copyfile(handoff/'evaluation-only/particles/natural-tick-0.npz',out/'world/initial-particles.npz')
    write_json(out/'inputs/activation-plan.json',{
        'episode_id':next(iter(pf['plan']['contexts_by_tick'].values()))['episode_id'],
        'resident_slots':[{'branch':'natural','tick':int(t),'agent_id':aid,
            'inclusion_probability':pf['plan']['panel_probabilities_by_tick'][t][aid]}
            for t,ids in pf['plan']['panels_by_tick'].items() for aid in ids],
        'governance_slots':[], 'timeline':list(range(10)), 'original_live_model':pf['model'],
        'original_live_particle_observation_model':'scope-calibrated-v3',
        'input_revision':3,'seed':pf['plan']['seed'],'same_tick_outputs_visible_only_after_commit':True})
    write_json(out/'events/contexts-by-tick.json',sanitized(pf['plan']['contexts_by_tick']))
    scenario=read(out/'events/scenario.json')
    scenario.update(branches=['natural'],ticks=list(range(10)),
        exogenous_updates='Three explicitly hypothetical updates at Tick1-3, unchanged thereafter',
        contexts_file='contexts-by-tick.json')
    write_json(out/'events/scenario.json',scenario)
    first=next(gzip_rows(handoff/'inputs/paired-observations.jsonl.gz'))
    write_json(out/'inputs/output-template.json',{
        **{k:first[k] for k in ('case_id','agent_id','branch','tick')},'method':'YOUR_BASELINE',
        'action':'read','visible_text':'','stance':'uncertain','emotion':'neutral',
        'parent_message_id':None,'quote_message_id':None,'pre_state':None,'post_state':None,
        'memory_update':'','is_template':True})
    write_json(out/'evaluation-only/latest-run-summary.json',sanitized(summary))
    latest_natural_metrics(out, world, pf, natural_dir)
    # Small structure probe remains a separate experiment; never pad 196 with it.
    probe=ROOT/'data/private/reddit/cmv/structure-three-chains'
    for name in ('completed-results.private.json','dimension-two-three-panel.private.json'):
        write_json(out/'evaluation-only/earlier-structure-probe'/name.replace('.private',''),sanitized(read(probe/name)))
    return {'paired_observations':196,'parent_state_rows':summary['parent_state_rows'],
            'particle_rows':summary['particle_rows'],'checkpoints':10,'branch':'natural','ticks':list(range(10))}


def latest_natural_metrics(out, world, pf, natural_dir):
    """Portable raw statistics and forecast rows; no independent scores invented."""
    from envs.campus_pulse_governance.reddit.evaluation import population_state_metrics, deduplicated_style_metrics
    from envs.campus_pulse_governance.reddit.particle_model import stance_predictive_probabilities
    progress=read(natural_dir/'progress.private.json')
    repo=CmvRunRepository(natural_dir/'full',run_id=progress['provider']['run_id'],
        plan_sha256=pf['plan_sha256'],world_sha256=pf['plan']['world_sha256'])
    state=repo.restore_latest('natural')
    if state is None or state.tick!=9: raise ValueError('Natural statistics require final checkpoint')
    cases=list(gzip_rows(out/'inputs/paired-observations.jsonl.gz'))
    ours=list(gzip_rows(out/'evaluation-only/ours-decisions.jsonl.gz'))
    if len(cases)!=196 or {r['case_id'] for r in cases}!={r['case_id'] for r in ours}:
        raise ValueError('Natural statistics pairing mismatch')
    snapshots={t:load_particle_snapshot(out/f'evaluation-only/particles/natural-tick-{t}.npz') for t in range(10)}
    forecasts=[]
    for row in cases:
        forecasts.append({**{k:row[k] for k in ('case_id','agent_id','branch','tick')},'condition':'full',
            'previous_tick_state':row['previous_tick_state'], 'decision_input_state':row['decision_input_state'],
            'previous_stance_probabilities':row['previous_stance_probabilities'],
            'pre_decision_stance_probabilities':row['pre_decision_stance_probabilities'],
            'post_decision_stance_probabilities':stance_predictive_probabilities(snapshots[row['tick']],row['agent_id'])})
    write_lines(out/'evaluation-only/state-forecast-panel.jsonl.gz',forecasts)
    observations=[{'tick':e['tick'],**sanitized(e['artifact'])} for e in repo._read_events()
                  if e.get('artifact_type')=='expression_observation_batch']
    if len(observations)!=9: raise ValueError('missing committed expression batch')
    write_json(out/'evaluation-only/assimilation-observations.json',{
        'used_for_assimilation':True,'independent_evaluation':False,'batches':observations})
    distributions={field:dict(Counter(r['output'].get(field,'omitted') for r in ours))
                   for field in ('action','emotion','stance','topic')}
    observed=Counter(r['expression_observation']['stance'] for b in observations for r in b['records'])
    if sum(observed.values())!=196: raise ValueError('expression coverage mismatch')
    frames=[{'tick':t,**population_state_metrics(snapshots[t],world)} for t in range(10)]
    metrics={'provider_calls':0,'resident_decisions':196,'unique_agents':len({r['agent_id'] for r in cases}),
        'native_output_distributions':distributions,'assimilation_label_counts':dict(observed),
        'state_frames':frames,'deduplicated_style':deduplicated_style_metrics(state.ledger,
            scenario_id=state.scenario_id,branch='natural'),
        'evaluation_status':{'independent_context_fact_profile_review':'not_performed',
            'independent_forecast_alignment':'awaits_separate_text_labels',
            'baselines':'not_run_in_this_campaign','governance':'Natural only; not applicable'},
        'state_forecasts':'state-forecast-panel.jsonl.gz',
        'observer_labels':'Assimilation observations are not independent outcome labels.'}
    write_json(out/'evaluation-only/latest-descriptive-metrics.json',metrics)
    return metrics


def source_export(out):
    """Complete current repository deliverables, excluding ignored local state."""
    raw=subprocess.run(['git','-c','core.quotepath=false','ls-files','-z','--cached','--others','--exclude-standard'],
                       cwd=ROOT,check=True,capture_output=True).stdout
    copied=[]
    blocked={'output','tmp','_run','.git','.agents','.codex','node_modules','dist','__pycache__',
             '.venv','playwright-report','test-results'}
    for name in sorted(set(raw.decode('utf-8').split('\0'))- {''}):
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or any(p in blocked for p in relative.parts):
            continue
        source=(ROOT/relative).resolve()
        if not source.is_relative_to(ROOT.resolve()) or not source.is_file(): continue
        if source.suffix.lower() in {'.sqlite','.sqlite3','.dpapi','.pyc','.log'} or relative.name=='API.txt': continue
        if relative.name.startswith('.env') or name=='config/model_config.json': continue
        if name.startswith('data/private/'): continue
        target=out/'source-code'/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        copied.append(name)
    write_json(out/'source-code-inventory.json',{'files':copied,'includes_uncommitted_source':True,
        'private_operational_state_included':False,'generation_calls':0})
    return len(copied)


def latest_documents(out, counts):
    """Replace legacy protocol text, not data, for the new Natural release."""
    (out/'README.md').write_text(f'''# Reddit Natural baseline 交接包

固定24名Agent、每人10粒子，最新结构的Natural自然演化运行Tick 1–9，共196个居民决策。对比议题为“医疗和教育应该免费”。背景人物使用事件前的跨线程历史；运行中的预算与补助变化是明确标记的假设条件。

## 快速核验

Python 3.11–3.13，在解压目录执行：

```text
python -m pip install -r requirements.txt
python tools/dataset.py verify
python tools/dataset.py example
python tools/dataset.py state-metrics --file evaluation-only/particles/natural-tick-9.npz
```

以上操作不调用API。先安装baseline自己的依赖，再接入本包接口。

## 目录

| 目录 | 内容 |
|---|---|
| corpus/ | 与上次相同的posts.csv、comments.csv、{counts['background']}条背景语料、{counts['candidate-histories']}条人物历史及240名候选人的匿名特征 |
| world/ | 固定24人、Profile、persona、历史、合成关系与初始24×10×12粒子 |
| events/ | 根帖、假设情景条件、每个Tick实际可见的事件背景 |
| inputs/paired-observations.jsonl.gz | 196条共同Natural观察，每条含实际人物输入、系统提示词、前序状态和可见论坛内容 |
| inputs/activation-plan.json | 9轮激活名单、逐人纳入概率、模型与初始设定 |
| inputs/output-template.json | baseline统一输出模板 |
| evaluation-only/ours-decisions.jsonl.gz | 与上述196条输入一一配对的CampusPulse真实输出 |
| evaluation-only/particles/ | Tick 0–9完整240粒子及其权重 |
| evaluation-only/combined_population_states.jsonl.gz | 24名父Agent的逐Tick加权状态，240行 |
| evaluation-only/full-particles.jsonl.gz | 逐粒子状态与两级权重，2400行 |
| evaluation-only/latest-run-summary.json | 本次运行用量及完成核验 |
| evaluation-only/latest-descriptive-metrics.json | 新运行的动作、情绪、风格及逐Tick群体状态统计 |
| evaluation-only/state-forecast-panel.jsonl.gz | 前序、实际输入及决策后概率，供同学配对独立正文评价 |
| evaluation-only/assimilation-observations.json | 本轮滤波用文本观测标签，单独保存，不充当独立评价标签 |
| evaluation-only/earlier-structure-probe/ | 之前108槽三条件小实验的诊断，独立保存，不填进本轮196条 |
| reference-src/ | 共享论坛合同、粒子、评估代码，供便携统计工具调用 |
| source-code/ | 当前完整项目源代码、前后端、测试、配置模板与文档；含本机未提交源码 |
| source-code-inventory.json | 此次源代码文件清单 |
| tools/dataset.py | 完整性校验、输入加载、baseline输出校验与原统计入口 |

本包不携带密钥、个人模型配置、依赖安装目录或Provider原始HTTP记录。筛选语料格式与上次保持一致；不再使用旧110槽结果作为新版观察。

## baseline怎么跑

推荐196条严格配对观察：三个方法都读取相同case_id的输入，按论文机制构建各自认知与记忆，然后保存自己的输出。只替换方法机制，不额外添加未来信息，不把CampusPulse粒子作为baseline的内在状态。Generative Agents等无显式数值状态的方法，相关指标按不适用处理。

每条输入记录保留同样的case_id、agent_id、branch=natural、tick。每方法需要恰好196个输出。不要合并三个预实验条件，也不要把阅读或沉默删掉。

该对比回答“同样所见信息下不同机制怎样回应”；如果比较各方法自由演化，则各自维护论坛、记忆及状态，下一Tick读取各自生成内容，不能使用固定输入面板冒充自由传播。

三个baseline的论文实现由接手同学维护。当前包验证工程路径和可复算数据，不预设CampusPulse胜出。最新108槽诊断中人物语言区分仍弱；196槽新运行的独立语义评价另行完成。

## 输出校验

baseline输出为JSONL，保存method、action、visible_text、stance、emotion，以及自身原生pre_state/post_state：

```text
python tools/dataset.py validate-outputs --file results/belief-engine.jsonl
```

输入只使用world、events、inputs；评价阶段再读取evaluation-only。上下文承接、虚假事实和正文立场由统一评审器判断，不使用模型自报立场替代正文。净立场方向、概率分类和更新后跟踪分别报告，详见BASELINE_PROTOCOL.md。
''',encoding='utf-8')
    (out/'BASELINE_PROTOCOL.md').write_text('''# 最新Natural对比协议

1. 先运行verify；三个方法均匹配同一196条输入。先用每方法2槽检查接口，不自动发起付费调用。
2. 采用相同模型、关闭思考、共同观察、动作合同和输出长度；保留论文对应的原生认知/记忆机制。
3. 输入case_id严格一一对应；前序状态、当轮实际输入状态、更新后状态分别保存。前序净立场方向是支持减反对，不是三类概率argmax。不同算子分别报告，不能沿用另一个数据集的中立半径。
4. 文本立场、上下文和事实评价使用同一独立评审器，保存可判定分母。阅读/静默与正文证据不足分开，不能当作反对。更新后跟踪不是预测成绩。
5. 风格按人物内标准化去重、字符1–3gram余弦算子计算；同时报告人物间相似度。按Agent分组切分训练/测试，不能把同一个人的不同Tick当成独立样本。
6. 群体统计在24名父Agent上合并自己的10粒子后计算，不能将240粒子当240人。切片为Tick0/3/9，报告总体SD、普通矩BC、KDE峰、20箱熵及12维有效秩；语义簇不能改称利益组。
7. 全体与实际激活面板分开统计。高熵、多个峰、高有效秩不是自动更好，不预设必须极化。
8. 自由演化和固定观察对比是两种实验，不混用。四条件Profile×状态消融由我们负责；这轮196条全部来自完整系统Natural。
9. 未知结果保留风险费用、弃用迟到响应并按照各自授权进行有界替代；有效输出持久化，恢复只补缺失。每个baseline的调用与费用另计，本包没有付费授权。
10. 交回逐条结果、原生状态/记忆、生成参数、失败修复用量、评审标签和统计面板。不得从evaluation-only读取我方答案构造baseline的当前决策。
''',encoding='utf-8')
    (out/'DATA_FORMAT.md').write_text('''# 数据格式

语料来源为ConvoKit发布的r/changemyview公开语料：https://convokit.cornell.edu/documentation/changemyview.html 。背景筛选窗口为2017年1–6月，保留跨主题历史。posts.csv和comments.csv沿用上次小喇叭式必需字段与UTF-8 BOM。

输入JSONL：case_id、agent_id、branch、tick、input、system_prompt、previous_tick_state、decision_input_state、previous_stance_probabilities、pre_decision_stance_probabilities。input为本轮实际可见论坛、Profile、历史、关系摘要、记忆与输出合同；system_prompt为实际系统提示词。

CampusPulse结果JSONL：同样的case_id、agent_id、branch、tick，method=CampusPulse，output为通过验证的原生输出。baseline输出按inputs/output-template.json扁平记录；保留各方法自己的原生内态和记忆。

粒子NPZ：agent_ids、values[24,10,12]、conditional_weights[24,10]、design_weights[24]及state_fields、tick、innovation。父Agent状态为每人内部10粒子的加权期望；群体统计对父Agent等权。纯观察对比196记录不等于196名独立人物。

运行Tick1–3包含假设预算100、成本130、修正成本120与首年20补助；Tick4–9未添加新事实。真实未来原帖仅在evaluation-only中留存，与合成假设条件分开，不用于评价假设条件的事实真值。

代码许可见LICENSE-code；Reddit语料的公开再分发需另核对来源条款，本包用于团队研究交接。
''',encoding='utf-8')


def build(out, *, resume=False, latest_natural=False):
    out=out.resolve()
    if not out.is_relative_to((ROOT/'output').resolve()): raise ValueError('output must be inside project output')
    world=read(BASE/'world-runtime-r2/fixed-world.private.json')
    if out.with_suffix('.zip').exists(): raise ValueError('refuse to overwrite a previous archive')
    if out.exists():
        marker=out/'world/fixed-world.json'
        if not resume or not marker.is_file() or read(marker).get('source_world_sha256')!=world['world_sha256']:
            raise ValueError('existing staging does not match this generated package')
    out.mkdir(parents=True,exist_ok=True)
    counts=corpus_export(out)
    future=event_export(out)
    exported=sanitized(world)
    for original,agent in zip(world['agents'],exported['agents']):
        agent['provider_profile_bundle']['profile_prompt_sha256']=original['provider_profile_bundle']['profile_prompt_sha256']
    exported['particle_asset']='initial-particles.npz'
    exported['source_world_sha256']=world['world_sha256']
    write_json(out/'world/fixed-world.json',exported)
    profiles=[{**sanitized(a['provider_profile_bundle']),'particle_count':10,'design_weight':a['design_weight'],
               'profile_prompt_sha256':a['provider_profile_bundle']['profile_prompt_sha256'],
               'semantic_cluster':a['semantic_cluster'],'persona_text':a['persona_text']} for a in world['agents']]
    write_json(out/'world/semantic-llm-forum-agent-population-v2.json',{
        'schema_version':'semantic-llm-forum-agent-population-v2','population_size':24,'particle_count':240,
        'compatibility_scope':'Agent profile field layout; campus 1000-person release gates are not claimed',
        'agents':profiles})
    write_json(out/'world/relationships.json',sanitized(world['relationships']))
    shutil.copyfile(BASE/'world-runtime-r2/initial-particles.private.npz',out/'world/initial-particles.npz')
    shutil.copyfile(BASE/'world-runtime-r2/initial-particles.private.npz',out/'world/background-initial-particles.npz')
    load_particle_snapshot(out/'world/initial-particles.npz')
    runtime=latest_natural_export(out,world) if latest_natural else runtime_export(out,world)
    # Source modules are the original implementation, not a rewritten baseline.
    source=ROOT/'src/envs/campus_pulse_governance'
    for path in source.rglob('*.py'):
        if 'tests' in path.parts or '__pycache__' in path.parts: continue
        target=out/'reference-src/envs/campus_pulse_governance'/path.relative_to(source)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    (out/'reference-src/envs/__init__.py').touch()
    (out/'tools').mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'scripts/templates/reddit_baseline_handoff_tools.py',out/'tools/dataset.py')
    shutil.copyfile(ROOT/'LICENSE',out/'LICENSE-code')
    (out/'requirements.txt').write_text('numpy>=1.26,<3\n',encoding='utf-8')
    readme=f'''# Reddit baseline 对比数据交接包

本包将 ChangeMyView 的已筛选语料、固定人物世界和正式观察面板整理为校园小喇叭项目使用的 CSV、Profile、粒子和逐条记录结构。当前对比议题是“医疗和教育是否应当免费”。24 名人物、240 个粒子、110 个独立居民观察槽位；这些不是小喇叭原始数据，也不是已运行的三个 baseline。

## 立即开始

需要 Python 3.10 以上。在解压目录运行：

```text
python -m pip install -r requirements.txt
python tools/dataset.py verify
python tools/dataset.py example
python tools/dataset.py state-metrics --file world/initial-particles.npz
```

以上校验和统计不调用 API。模型密钥由接手同学自行配置，本包不含密钥、Provider 请求包或 SQLite。

## 文件对应关系

| 目录或文件 | 用途 |
|---|---|
| corpus/posts.csv、comments.csv | 小喇叭预处理所需的同名必需字段；UTF-8 BOM |
| corpus/background.jsonl.gz | 全部筛选背景语料 {counts['background']} 条，保留匿名作者、父消息和真实时间 |
| corpus/candidate-histories.jsonl.gz | {counts['candidate-histories']} 条候选人物历史；使用相同作者映射 |
| corpus/candidate-authors.json | {counts['candidate_authors']} 名候选作者的匿名特征 |
| corpus/thread-manifest.json | 标出采样线程是否保留真实根帖 |
| world/fixed-world.json | 本次正式实验的固定人物、persona、历史摘要和关系 |
| world/semantic-llm-forum-agent-population-v2.json | 小喇叭 Profile 字段形式；数量是 24，不要求改成 1000 |
| world/agent-history-bindings.json | 24 名人物与匿名候选历史关联 |
| world/initial-particles.npz | 正式主实验事件绑定后 Tick 0 的 24×10×12 状态、条件权重和人口设计权重，baseline 从这里起跑 |
| world/background-initial-particles.npz | 背景世界的通用初值，仅供溯源；和事件绑定后的立场初值分开 |
| events/main-event.json、scenario.json | 当前议题与事件前缀；真正运行只在 Tick 0 注入根帖 |
| events/other-frozen-inputs.json | 早期另三个已筛选事件材料，未用于本主结果 |
| inputs/paired-observations.jsonl.gz | 当前真实的 110 个时间可见输入，含 Profile、历史、前序状态和已看到的论坛内容 |
| inputs/activation-plan.json | 主实验冻结激活名单、时间步和治理槽位 |
| inputs/output-template.json | baseline 逐条输出模板 |
| evaluation-only/ | 真实未来续帖、我方结果、盲评标签和完整逐轮粒子；禁止放进生成输入 |
| reference-src/ | 原工程共享论坛合同、粒子模型和评估代码，仅供接入和复算，不是 baseline 实现 |
| tools/dataset.py | 校验、加载例子、输出格式校验和原统计函数入口 |

固定背景窗口为 2017 年 1–6 月。背景语料保留公开自然文本但移除原始记录 ID、作者键、用户链接、网址和邮箱；所有身份为稳定匿名别名。人物画像来自事件前多讨论串历史。数据来源为 ConvoKit 发布的 r/changemyview 语料，获取地址见 DATA_FORMAT.md。代码许可见 LICENSE-code；代码 Apache 许可不自动授予 Reddit 文本再分发权。此包面向团队研究交接，公开发布数据前另行确认来源条款。

## 实验执行选择

首先推荐 **110 槽严格配对观察对比**：对每条输入使用相同人物、事件、可见讨论和历史；按方法替换认知/记忆机制，保留原论坛动作语义。三个方法都输出同一个 case_id、agent_id、branch、tick。当前记录含自然和治理条件，不是三个方法已生成的新文本。

该模式测量给定相同讨论输入下的方法响应，不能报告成各 baseline 自由演化的传播结果。共享前缀只有 10 条且只计一次。每种方法目标 110 居民输出；需要格式修复时单独记录请求和 Token。不得读取 evaluation-only 来补人物立场、反思或决定当前行为。

如果开展 **自由演化对比**，从固定世界、初始状态和事件根帖开始，沿 activation-plan 选择相同 Agent。后续帖子、记忆和状态必须由各方法自己的前轮结果推进；不能继续使用我方固定观察面板来声称 baseline 自由传播。Tick 1 共享，Tick 2–6 分为 natural/D。治理策略与观测协议也要共享冻结，或明确只比较自然分支，不能给某方法额外未来信息。每 Tick 所有请求读同一个提交前快照，同 Tick 新发言下 Tick 才可见。

三个 baseline 的论文实现由接手同学按其原论文/源码接入。本包提供数据和接口，没有把我方 SMC 更新冒充 Belief Engine，也没有把普通历史摘要冒充 Generative Agents 的记忆反思。详细任务顺序见 BASELINE_PROTOCOL.md。

## 结果保存与校验

将各方法输出存为 JSONL，每条至少包含模板中的身份字段、method、action、visible_text：

```text
python tools/dataset.py validate-outputs --file results/belief-engine.jsonl
```

此校验要求恰好覆盖 110 个输入，没有重复、身份变化或缺失；不生成缺失答案。评价时由统一外部模型读取真实输出文本，再计算上下文承接、无依据事实和表达立场，不用结构化自报立场代替文本评审。

本包当前主实验：上下文承接 99.05%、无依据事实 0/104、前序状态吻合 26.21%。按独立 ±0.05 口径重算的决策后自洽为 39.81%。完整指标表在 evaluation-only/data-panels.md；该表空白 baseline 列等待本次新实验填写。
'''
    (out/'README.md').write_text(readme,encoding='utf-8')
    (out/'DATA_FORMAT.md').write_text('''# 数据字段与兼容说明

原始来源：https://zissou.infosci.cornell.edu/convokit/datasets/subreddit-corpus/corpus-zipped/cats3~-~changemyview/changemyview.corpus.zip

## CSV 和清洗语料

posts.csv 的必需字段为 post_code、real_date、content；comments.csv 为 post_code、comment_id、real_date、floor、content，与工程 forum_data.py 的读取器一致。post_code、comment_id 全部为导出匿名 ID，不是 Reddit ID。

背景是分层抽样，不保证所有线程的根帖和祖先都保留。缺失根帖在 thread-manifest 的 root_available=false 明确标注，没有用标题伪造根帖正文。孤立采样评论仍完整保存在 JSONL 和 comments.csv；只支持完整线程的旧清洗器可能排除这些评论，因此人物历史读取 candidate-histories.jsonl.gz，不要重新依赖 CSV 线程重建编译人物。floor 是采样后按真实时间稳定排序的楼层，不代表原网站楼层；CSV real_date 采用父帖日期或最早保留记录日期，真实发言时间在 actual_timestamp_utc/JSONL timestamp_utc。已发布文本重复但作者不同的记录不按文字全局删除，以保留真实表达频率。

## 固定人物与状态

Agent ID 使用 CMV-A01 至 CMV-A24；persona、stable_persona、历史 episode focus 和事件输入分别保存。使用同样的 24 人及关系。design_weight 为人口设计权重，conditional_weights 是每人 10 粒子的条件权重，不能混用。NPZ 顺序由 agent_ids 和 state_fields 决定，allow_pickle=False。

12 维依次为 attention、concern、trust、satisfaction、support、opposition、activity、voice、service_strain、rumor_belief、verified_belief、evidence_acceptance，范围 [0,1]。support+opposition<=1。净立场为支持减反对，先人物内部加权，再在 24 人上统计。240 粒子不作为 240 名独立人物。

原始主实验使用旧滤波和旧实际输入；reference-src 含后续方向滤波修复。activation-plan 明确记录旧实验使用的版本。不要把新修复代码重放结果写成旧实验实时生成。输入面板保留原来实际 agent_prompt 的结构，新的公平 baseline 共有字段改动必须对所有方法同步冻结。

## 时间与输出

main-event 保留观察前缀的 created_order，其顺序不是猜出的实际父回复链。只有真实根帖作为外生公共初始消息进入 Tick 0；当前模型能看到的前缀/上下文以 paired-observations 为准。真实未来198条续帖只在 evaluation-only。已知根帖事后 Update 已移除，但源语料没有编辑历史，不能声称恢复了当时逐字版本。

baseline 每条输出包含 case_id、agent_id、branch、tick、method、action、visible_text、stance、emotion、parent_message_id、quote_message_id、memory_update，并保存方法原生 pre_state/post_state。自报 stance 的 support/oppose 针对事件整体命题；若模型同意某个反对者，不能把“同意此回复”直接当作支持整个议题。方法没有显式数值状态时 pre_state 留空，而不是用我方粒子代填。

评价期输出与冻结输入 case_id 一一对应。模型自己生成的内容、记忆和反思必须保存，但密钥、Authorization 和 HTTP body 不提交到数据包。
''',encoding='utf-8')
    (out/'BASELINE_PROTOCOL.md').write_text('''# 三个 baseline 接手步骤

1. 执行 README 的校验。选定严格配对观察或自由演化，报告中分开，不混为一种实验。
2. 分别获取 Belief Engine、Generative Agents、Chuang 等方法的论文和源码，固定 revision、依赖和实现差异。本交接未核验新取得的仓库是否等价于论文，不能以本包说明替代实现审计。
3. 输入人物、事件、论坛动作、可见时间、模型、生成参数和 Token 限制在各方法之间一致；各方法认知/记忆表示与更新算法保留原机制。无需强迫 Generative Agents 拥有 10 个数值粒子。禁止为了结果优劣替某方法降低上下文或追加未来证据。
4. 先每方法 2 个槽位验证 JSON 和状态记录，再执行选定面板。已提交输出持久化，恢复只补缺失槽位。网络 unknown 不盲重试；同学单独配置自己的授权/预算，包内没有付费授权。
5. 保存每条原生输入、输出、状态、记忆、模型和用量。生成输入不读取 evaluation-only 的结果或真实后续；评价器可以在生成结束后读取。
6. 统一评审上下文承接、事实编造、文本立场。前序吻合读取上 Tick 决策完成后的原生内态，当前表达不反写进前序。前序中立半径只从原生状态分布冻结，当前 CampusPulse 为0.09128867750243846；其他方法单独按80%分位校准后固定。后序 ±0.05 与前序分开。无显式状态的方法该指标 NA。
7. 风格统计按人物内 NFKC、大小写/空白归一删除完全重复文本，再计算字符1–3gram相似度；合格人物至少10名。同时看人物间相似度，不能只报告人人都高相似。
8. 维度三使用固定24人，切片Tick0/3/6。报告均值、总体SD、普通矩BC、KDE、20箱自然对数熵。语义簇不冒充利益组；当前无利益组标签，该列NA。对生成式方法用其原生意见算子或统一独立问卷，额外问卷调用预算单列。不要用我方输入状态或我方原始粒子填baseline输出状态。
9. 真正四条件生成式消融尚未运行；特征预测消融、固定发言滤波消融不能填进四条件生成式表。情绪原生八类与动作类别在所有方法中统一映射，并保留原字段。
10. 汇总提交：三个方法的逐条输出、各自状态或意见测量、评审标签、运行参数、失败/修复用量和指标面板。不预设哪一个必须最佳。
''',encoding='utf-8')
    if latest_natural:
        latest_documents(out,counts)
        source_files=source_export(out)
        runtime['source_files']=source_files
    # Detect real credentials and host paths in the allowlisted package before release.
    credential=re.compile(rb'(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9_-]{20,}')
    for path in out.rglob('*'):
        if not path.is_file(): continue
        data=gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
        scan_data=data
        if path.relative_to(out).as_posix()=='source-code/src/backend/tests/test_campus_pulse_live_transport.py':
            # This exact fixture is deliberately rejected by the transport
            # test. It is not a credential and the test is shipped unchanged.
            scan_data=scan_data.replace(b'Bearer '+b'never-persist-bearer',b'<transport-negative-test>')
            scan_data=scan_data.replace(b'Bearer '+b'never-persist-secret',b'<transport-negative-test>')
        if credential.search(scan_data): raise ValueError('credential pattern in package: '+path.relative_to(out).as_posix())
        if 'source-code' not in path.relative_to(out).parts and path.suffix in {'.json','.jsonl','.md','.csv'} and re.search(rb'[A-Za-z]:[\\/](?:code|Users)',data):
            raise ValueError('local path in package: '+path.name)
    write_json(out/'manifest.json',{'schema':'cmv-baseline-team-handoff-v1','provider_calls':0,
        'corpus_counts':counts,'evaluation_only_main_continuation':future,'runtime_counts':runtime,
        'files':_file_hashes(out,exclude={'manifest.json'}),'original_data_preserved':True,'input_corpus_policy':'processed local subset, not entire downloaded archive'})
    _zip_tree(out,out.with_suffix('.zip'))
    return {'directory':str(out),'zip':str(out.with_suffix('.zip')),'zip_bytes':out.with_suffix('.zip').stat().st_size,
            'corpus':counts,'runtime':runtime,'provider_calls':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/Reddit-Baseline-Handoff-20261001')
    parser.add_argument('--resume',action='store_true',help='Rebuild only a matching incomplete generated staging directory')
    parser.add_argument('--latest-natural',action='store_true',help='196 new Natural observations and complete current source')
    args=parser.parse_args()
    print(json.dumps(build(args.output,resume=args.resume,latest_natural=args.latest_natural),ensure_ascii=False))
