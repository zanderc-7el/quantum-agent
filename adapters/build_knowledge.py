"""Turn verified field lessons into scoped, versioned controller rule records."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('C:/Users/o/Downloads/量子测控智能体工程包_原流程15步版_20261024/量子测控智能体工程包_原流程15步版_20261024')


def build():
    target = ROOT / 'knowledge'
    (target / 'sources').mkdir(parents=True, exist_ok=True)
    refs = {}
    for name in ['knowledge/field_case_20261117_q11.md', 'knowledge/field_case_20261117_q18_q19.md', 'knowledge/parameter_dependencies.md', 'knowledge/field_review_20261116_152511.md', 'docs/supervisor_policy.md']:
        source = SOURCE / name
        dest = target / 'sources' / source.name
        shutil.copyfile(source, dest)
        refs[source.stem] = {'sourcePath': str(source), 'localPath': str(dest.relative_to(ROOT)), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    definitions = [
        ('capture_limit', ['s01_readout_frequency','s02_qubit_frequency','s03_rabi_amplitude','s04_readout_fidelity','s05_readout_optimization','s06_t1','s07_t2','s08_single_qubit_rb'],
         '按本机有效采集段数分批', '本机 CY 硬 IQ 请求 shots > 2000，或尾段为复零', 'reject_and_split_capture', '每次 capture ≤ 2000，独立重复并检查逐段有效性；不把上限泛化到其他设备', 'field_case_20261117_q11', {'field':'shots','operator':'gt','value':2000}),
        ('slow_rabi', ['s02_qubit_frequency','s03_rabi_amplitude'],
         '弱响应先检查搜索覆盖', 'Rabi 未识别，时间窗不足或筛选下限高于潜在振荡频率', 'redesign_scan', '扩展真实时间窗、细化频点；随机时间和交错双幅度的新采集验证；保存未通过曲线', 'field_case_20261117_q11', {'field':'identifiable','operator':'eq','value':False}),
        ('readout_change', ['s04_readout_fidelity','s05_readout_optimization','s08_single_qubit_rb'],
         '读出更新后重建独立参考', '读出频率、幅度、窗口或分类器版本变化', 'invalidate_dependents', 's05 更新后回 s04 新训练/独立验证；旧参考不能换成事后新投影冒充冻结验证', 'parameter_dependencies', {'field':'readout_version_changed','operator':'eq','value':True}),
        ('frequency_change', ['s02_qubit_frequency','s03_rabi_amplitude','s07_t2','s08_single_qubit_rb'],
         '改频后复校旋转与门基准', '驱动频率已提交新版本', 'backtrack', 's02 验证实际修正符号；s03 复测脉冲；随后对受影响 s08 重新基准', 'parameter_dependencies', {'field':'drive_version_changed','operator':'eq','value':True}),
        ('ramsey_sign', ['s02_qubit_frequency','s07_t2'],
         'Ramsey 符号按对象取证', '拟合给出频差，但目标对象/驱动约定无已验证修正符号', 'wait_for_signcheck', '对当前对象施加已知实际改频验证方向；Q18 不继承 Q11 符号；±旋转联合模型先检查和式自洽', 'field_case_20261117_q18_q19', {'field':'frequency_sign_verified','operator':'eq','value':False}),
        ('fit_not_fidelity', ['s03_rabi_amplitude','s04_readout_fidelity','s08_single_qubit_rb'],
         '门保真度使用专属证据', '仅有 Rabi 半周期、AllXY RMS 或读出制备标签准确率', 'restrict_claim', '保持各物理量定义；单门性能另用完整独立 RB/IRB，不将 RMS 或图形外观换算保真度', 'field_case_20261117_q11', {'field':'gate_fidelity_evidence','operator':'missing'}),
        ('rb_paired_statistics', ['s08_single_qubit_rb'],
         'RB 以完整配对随机序列为统计单元', 'reference/IRB 共享 frozen seed 且含内层 captures', 'validate_paired_evidence', '整条长度向量和配对分支一起 bootstrap；captures/shots 不是新增随机序列；保留漂移与模型条件', 'field_case_20261117_q18_q19', {'field':'rb_pairing_verified','operator':'eq','value':False}),
        ('model_identifiability', ['s02_qubit_frequency','s03_rabi_amplitude','s06_t1','s07_t2'],
         '不可辨识参数保持空值', '拟合撞边、多峰、不到一周期或重复预测失败', 'redesign_scan', '新的扫描设计/独立复测通过前，保留 null；不以优化器边界当已测频率', 'field_case_20261117_q18_q19', {'field':'fit_boundary_hit','operator':'eq','value':True}),
        ('unknown_operation', ['supervisor'],
         '超时后先核对回执', '已发送操作缺完成回执或硬件停止未经确认', 'reconcile', '保留资源隔离；只有确认未执行才重发；不能把客户端超时解释为未执行', 'supervisor_policy', {'field':'hardware_state','operator':'eq','value':'unknown'}),
        ('power_step', ['s01_readout_frequency'],
         '功率扫描按真实响应评估', '功率图平坦或未见与示例相同阶梯', 'redesign_scan', '先查对象/功率参考面/扫描覆盖与噪声，再在既定范围内扩扫；不要求所有腔出现相同阶梯', 'field_review_20261116_152511', {'field':'power_response_identifiable','operator':'eq','value':False}),
    ]
    rules = [{'id':'field.'+ident, 'version':1, 'title':title, 'scope':scope, 'trigger':trigger, 'action':action,
              'verification':verification, 'evidence':[refs[src]['localPath']], 'sourceSha256':refs[src]['sha256'],
              'predicate':predicate, 'policyStatus':'scoped_candidate_rule', 'thresholdSemantics':'Device-specific numerical gates require an explicit controller policy; prose alone cannot pass a measurement.'}
             for ident, scope, title, trigger, action, verification, src, predicate in definitions]
    value = {'version':1, 'rules':rules, 'sources':refs, 'originalWorkflow':'15 owners + supervisor',
             'notes':['案例数据是历史证据，不是实时硬件状态。','归档文章中的链接保留原来源；本项目只复制引用文章，完整证据仍在源工程包。','NA 先导/换线交接与 ADC 主流程分列。']}
    (target / 'rules.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return value


if __name__ == '__main__':
    print(json.dumps({'rules':len(build()['rules'])}))
