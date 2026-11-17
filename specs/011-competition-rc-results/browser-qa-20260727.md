# CampusPulse Competition RC browser QA

Date: 2026-07-27  
Scope: frozen competition frontend, internal single-node workbench, offline fallback  
Provider calls during QA: 0

## Environment boundary

- The complete authorized-server tree was used for the production build.
- Browser access used loopback-only port forwarding; neither the API nor the
  offline server was exposed on a public interface.
- Workbench mutation used an isolated `/tmp` SQLite database created only for
  this QA. No existing workspace database was reused or modified.
- No credential, Provider transport, raw forum row or record-level review text
  was read by the browser.

## Production build and offline identity

Command:

```bash
make campus-pulse-demo-check \
  CAMPUS_PULSE_ONLINE_PYTHON=.venv-campus-pulse-online/bin/python
```

Observed result:

- Vite: 2,154 modules transformed, production build passed;
- build files: 49;
- direct entry assets: 6;
- total bytes: 13,925,904;
- build fingerprint:
  `641638e8167433fb65cc5ae70ed3cca802a96c67cf2aa64ce7432a727b56dcef`;
- health mode: `competition_offline_static`;
- `api_required=false`;
- `workbench_available=false`;
- `provider_calls=0`.

The check also proved that all direct `/assets/` references exist, forbidden
private file suffixes are absent, fixed sensitive markers are absent, the SPA
route returns HTML, and health output matches the audited build identity.

## Desktop competition path

The real production page at `/campus-pulse` loaded without console warnings or
errors. The following actions were performed through visible controls:

1. switched from the governance-action shock to the external lecture shock;
2. confirmed the selected heading changed to `讲座争议外生事件`;
3. switched the external shock to `03 扩散 / 讨论进入论坛`;
4. switched back to the Tongzhou shock;
5. changed the policy branch from `自然演化` to `组合治理`;
6. confirmed the policy service contract became visible;
7. selected agent 48 and confirmed the detail prototype changed to
   `身心健康求助者`.

The hero, 48-agent population, four-stage replay, reviewed utterances, local
thread ecology, policy branch comparison, cross-shock transfer and evidence
boundary were all present in the DOM.

## Internal workbench path

The internal workbench connected to the API without console warnings or
errors. In an isolated database, QA:

1. created project `参赛彩排：双冲击治理压力测试`;
2. loaded the reviewed dual-shock template;
3. confirmed 2 frozen scenarios, 2 policy plans, 1 source evidence snapshot
   and 1 reviewed sensing snapshot;
4. confirmed the source registration showed 132,917 post rows, 454,053
   comment rows and 99.19% comment-to-post association;
5. created a 48-agent, 32-worker, two-seed audited-replay plan;
6. confirmed the plan remained `draft`, Provider calls remained 0 and the UI
   correctly disabled enqueue because the runtime adapter was not enabled.

This proves online record creation and restoration, not a live model result.

## Offline fallback and deep links

The production build was served by
`scripts/campus_pulse/run_competition_demo.py` on loopback only.

- `/campus-pulse` loaded with title
  `CampusPulse · 代表人群治理推演`;
- `/campus-pulse/robustness` survived a direct load and browser refresh;
- both pages loaded their precomputed data without console warnings or errors;
- `/campus-pulse/workbench` returned a deliberate offline notice rather than a
  broken API-dependent screen;
- the notice's `返回主演示` link returned to `/campus-pulse`;
- a missing `/assets/` file returned HTTP 404 rather than the SPA shell.

## Mobile layout

At an explicit 390×844 viewport:

- `window.innerWidth=390`;
- `document.body.scrollWidth=390`;
- `document.documentElement.scrollWidth=390`;
- the hero heading stayed within x=17.0 to x=369.7;
- no horizontal overflow was observed;
- the primary replay action and both supporting actions remained visible.

## Automated evidence

- offline server unit tests: 4/4 passed with `ResourceWarning` promoted to an
  error;
- governance/simulation suite: 152/152 passed;
- existing backend gate: 299/299 passed;
- frontend contract gate: 11/11 passed;
- production build: 2,154 modules transformed, passed.

After synchronization, the final release audit reran the full backend,
simulation, frontend and production-build/offline gates. All counts above
passed on the same authorized-server working tree.

## Release boundary

- The offline launcher does not start an API, runtime worker or Provider.
- The offline notice makes the workbench dependency explicit.
- The deterministic P3.2b fixture remains non-exportable and must not be shown
  as a model output or governance finding.
- Real Provider canary execution remains a separately authorized P3.2c task.
- Historical window comparisons remain descriptive, not causal, real-time,
  population-level or predictive claims.
