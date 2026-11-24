"""Evidence-bounded presentation of stopped acquisitions; no device/file I/O."""


def stopped_summary(receipts, *, selection_retained=False):
    receipts = [r for r in receipts if isinstance(r, dict)
                and r.get('status') in ('cancelled', 'failed_after_shutdown')
                and r.get('shutdownConfirmed') is True]
    if not receipts:
        return None
    artifacts = [a for r in receipts for a in r.get('partialArtifacts', []) if isinstance(a, dict)]
    raw = any(a.get('kind') == 'raw' for a in artifacts)
    counts = [r.get('pointsCompleted') for r in receipts]
    counts_known = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts)
    points = sum(counts) if counts_known else None
    connection_failed = any('DeviceConnectionError' in str(r.get('reason', ''))
                            or 'VI_ERROR_RSRC_NFOUND' in str(r.get('reason', '')) for r in receipts)
    measurement = 'partial' if raw else 'none' if points == 0 else 'unconfirmed'
    if measurement == 'partial':
        message = '采集已停止，部分原始数据已保留，尚未完成独立验证。'
    elif measurement == 'none':
        message = ('设备连接失败' if connection_failed else '采集已停止') + '，本次未获得测量数据。'
        if artifacts:
            message += '已保留执行日志和诊断文件。'
    else:
        message = ('设备连接失败' if connection_failed else '采集未完成') + '，尚无可核验的原始数据。'
        if points is not None:
            message += '回执记录已采集 ' + str(points) + ' 个点，原始数据留存尚待核查。'
    if selection_retained:
        message += '已完成的候选采集单独保留；本次独立复测尚未验收。'
    # Cleanup of opened resources is not a physical output-state readback.
    message += '已记录执行退出与资源清理回执；未核验仪器输出状态。'
    return {'version': 1, 'reason': 'connection_failed' if connection_failed else 'acquisition_stopped',
            'measurementData': measurement, 'rawDataAvailable': raw, 'pointsCompleted': points,
            'selectionRetained': bool(selection_retained), 'message': message}
