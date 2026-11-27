import test from 'node:test';
import assert from 'node:assert/strict';
import {qualityDecisionPoints,qualityExplanation} from '../src/lib/qualityPresentation.ts';

test('observed spectrum failure shows readable thresholds without changing unknown diagnostics',()=>{
 const raw="Paired drive response candidate does not repeat away from scan boundaries; peak_snr=3.40016 不满足 {'min': 5}; 需要独立采集复测";
 const rendered=qualityExplanation(raw);
 assert.match(rendered,/尚未确认可重复的谱峰，请查看各次采集结果/);
 assert.doesNotMatch(rendered,/边界|内部峰/);
 assert.match(rendered,/峰值信噪比 3.40 低于 5/);
 assert.match(rendered,/尚未完成独立采集复测/);
 assert.doesNotMatch(rendered,/peak_snr|Paired drive/);
 assert.equal(qualityExplanation('New native diagnostic code 87'),'New native diagnostic code 87');
 assert.equal(qualityExplanation("unknown_metric=2 不满足 {'min': 5}"),"unknown_metric=2 不满足 {'min': 5}");
});

test('unscoped native reasons are described as a set of candidate exclusions',()=>{
 const raw='95% model interval is too wide relative to the fitted parameter.；At least two separately acquired, valid repeat fits are required.；Fit reached a frequency/time optimization boundary.；Fitted oscillation contains fewer than 1.5 cycles.；Insufficient BIC improvement over constant/linear-trend models (<10).；Oscillation amplitude is not resolved relative to residual/shot noise.；缺少指标 contrast_snr；缺少指标 cycles；缺少指标 r_squared；需要独立采集复测';
 const points=qualityDecisionPoints(raw);
 assert.equal(points.length,4);
 assert.match(points[0],/不同拟合候选的筛除记录.*不足 1\.5 个振荡周期.*振幅与残差或采样噪声难以区分.*95% 拟合区间过宽.*搜索边界.*常数或线性趋势模型，BIC 改善不足 10/);
 assert.match(points[1],/无法确定还缺几组/);
 assert.equal(points[2],'缺少有效指标：对比度信噪比、振荡周期数、拟合决定系数（R²）。');
 assert.match(points[3],/至少两组独立采集.*各组拟合均有效.*尚未完成独立采集复测/);
 assert.doesNotMatch(points.join(' '),/contrast_snr|cycles|r_squared|95% model/);
 assert.equal(qualityExplanation(raw.split('；')[0]),'参数的 95% 拟合区间过宽');
});

test('Rabi capture counts and selected-fit cycles never attribute aggregate rejections to a specific fit',()=>{
 const reason='At least two separately acquired, valid repeat fits are required.；Insufficient BIC improvement over constant/linear-trend models (<10).；需要独立采集复测';
 const evidence={schema:'qcal-rabi-fit-display/1',stage:'measure',requiredValid:2,captures:[{index:1,valid:true,cycles:3.174},{index:2,valid:false}]} as const;
 const rows=qualityDecisionPoints(reason,{...evidence,captures:[...evidence.captures]});
 assert.match(rows[0],/首测共 2 组采集，1 组得到有效拟合，还缺 1 组/);
 assert.match(rows[1],/第 1 组的有效拟合覆盖 3.17 个周期。第 2 组未得到有效拟合/);
 assert.match(rows[2],/不同拟合候选的筛除记录.*BIC 改善不足 10/);
 assert.doesNotMatch(rows.join(' '),/第 2 组.*BIC 改善为|首要原因|最佳候选/);
 assert.match(rows.at(-1)!,/尚未完成独立采集复测/);
 const none=qualityDecisionPoints(reason,{...evidence,captures:[{index:1,valid:false},{index:2,valid:false}]});
 assert.match(none[0],/0 组得到有效拟合，还缺 2 组/);
 assert.match(qualityDecisionPoints(reason,{...evidence,captures:[{index:1,valid:true,cycles:NaN},{index:2,valid:false}]}).join(' '),/无法确定还缺几组/);
});

test('missing values, below-threshold values, and unknown reasons remain distinct',()=>{
 assert.deepEqual(qualityDecisionPoints('缺少指标 cycles；缺少指标 cycles；New fitter code 87；缺少指标 cycles_extra'),['缺少有效指标：振荡周期数。','New fitter code 87','缺少指标 cycles_extra']);
 const original="cycles=0.87 不满足 {'min': 1}; contrast_snr=4.4 不满足 {'min': 5}";
 assert.equal(qualityExplanation(original),'振荡周期数 0.87 低于 1; 对比度信噪比 4.40 低于 5');
 assert.deepEqual(qualityDecisionPoints('New fitter code 87;  native field=89'),['New fitter code 87;  native field=89']);
 assert.deepEqual(qualityDecisionPoints('缺少指标 toString'),['缺少指标 toString']);
 assert.equal(qualityExplanation("toString=2 不满足 {'min': 5}"),"toString=2 不满足 {'min': 5}");
 assert.equal(qualityExplanation("r_squared=0.7996 不满足 {'min': 0.8}"),'拟合决定系数（R²） 0.7996 低于 0.8');
 assert.deepEqual(qualityDecisionPoints('需要独立采集复测'),['尚未完成独立采集复测。']);
});

test('missing verification frequency candidate is readable without changing unknown keys or raw reason',()=>{
 const raw='独立复测缺少同一候选参数：drive_hz; New native diagnostic code 87';
 assert.equal(qualityExplanation(raw),'独立复测缺少同一候选参数：驱动频率; New native diagnostic code 87');
 assert.equal(raw,'独立复测缺少同一候选参数：drive_hz; New native diagnostic code 87');
 assert.equal(qualityExplanation('独立复测缺少同一候选参数：drive_hz_extra'),'独立复测缺少同一候选参数：drive_hz_extra');
 assert.equal(qualityExplanation('独立复测缺少同一候选参数：new_parameter'),'独立复测缺少同一候选参数：new_parameter');
});
