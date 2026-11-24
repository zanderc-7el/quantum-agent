from typing import Optional
from matplotlib import pyplot as plt
import numpy as np
import re
from .pulse import *
from .wave import *
from io import BytesIO
import base64
from scipy import stats
from matplotlib.patches import Ellipse
from matplotlib.colors import LinearSegmentedColormap

barrier_style = {
    'barrier_line_color': '#7f7f7f',
    'barrier_color': '#7f7f7f',
    'barrier_line_style': "--",
    'barrier_alpha': 0.18,
    'barrier_line_per_qubit': True,
}
seq_color_map = {
    "xy":  "#1f77b4",  # C0  单比特XY
    "z":  "#9467bd", # C1  单比特Z
    "cz":     "#2ca02c",  # C2  CZ/Flux
    "read":   "#d62728",  # C3  读出
}

def plot_sequence(sequence, remap_time=True, ax: Optional[plt.Axes] = None, auto_size = False, given_size = None, to_base64 = False):
    """
    序列可视化

    Args:
        sequence (_type_): 需要可视化的序列
        remap_time (bool, optional): 是否需要对时间轴做缩放. Defaults to True.
        ax (Optional[plt.Axes], optional): 画布区域. Defaults to None.
        auto_size (bool, optional): 是否自动调整大小. Defaults to False.
        given_size (_type_, optional): 画布大小. Defaults to None.
        to_base64 (bool, optional): 是否转化为base64
    
    对于数据很多的图，如果想看清的话需要手动设置图片大小: plt.plot(figsize=(n,m))
    autosize == True时图片会自动设置长度，用于显示很长的sequence
    """
    
    new_sequence = []
    for pulse_tuple in sequence:
        new_sequence.append((pulse_tuple[0], round(pulse_tuple[1]*1e9,1), round(pulse_tuple[2]*1e9,1), pulse_tuple[3], pulse_tuple[4]))

    sequence = new_sequence

    moments_org2rmp, moments_rmp2org = remap_to_indices(
        moment
        for (_, start_time, duration, _, _) in sequence
        for moment in (start_time, start_time + duration)
    )

    xlen = len(moments_rmp2org)
    if auto_size == True:
        plt.figure(figsize=(int(xlen*1.5),5))
    elif given_size != None:
        plt.figure(figsize=given_size)

    sequence = tuple(sequence)
    ax = plt.gca() if ax is None else ax


    legend_flag = [None] * 5

    qubit_org2rmp, qubit_rmp2org = remap_to_indices(
        q
        for (qubits, start_time, duration, _, _) in sequence
        for q in iter_int_or_tuple(qubits)
    )
    
    for qubits, start_time, duration, pulse_type, pulse_instance in sequence:
        #start_time = round(start_time, 3)
        qubits = sorted(list(iter_int_or_tuple(qubits)))
        if remap_time:
            l = moments_org2rmp[start_time]
            r = moments_org2rmp[start_time + duration]
        else:
            l = start_time
            r = start_time + duration
        w = r - l
        h = 4 / 5

        # ---------- Barrier 渲染 ----------
        if isinstance(pulse_instance, Barrier):
            if barrier_style['barrier_line_per_qubit']:
                hh = (4/5)/2
                for q in qubits:
                    rq = qubit_org2rmp[q]
                    y0, y1 = rq - hh, rq + hh
                    ax.vlines(l, y0, y1, color=barrier_style['barrier_line_color'], ls=barrier_style['barrier_line_style'],
                        lw=2.0 * 3.0, alpha=0.3, zorder=3.0)
                    ax.vlines(l, y0, y1, colors=barrier_style['barrier_line_color'],
                            linestyles=barrier_style['barrier_line_style'], linewidth=3.0, zorder=3.1)
            else:
                ax.axvline(l, color=barrier_style['barrier_line_color'], ls=barrier_style['barrier_line_style'],
                   lw=2.0 * 3.0, alpha=0.3, zorder=3.0)
                ax.axvline(l, color=barrier_style['barrier_line_color'], ls=barrier_style['barrier_line_style'],
                        lw=3.0, zorder=3.1)
            
            if legend_flag[4] is None:
                legend_flag[4] = plt.Line2D([0], [0], color=barrier_style['barrier_line_color'],
                                                ls=barrier_style['barrier_line_style'], lw=3.0)
            
            continue
        # ----------------------------------

        polygon_xy = []
        for q in qubits:
            remapped_qubit = qubit_org2rmp[q]

            cx = (l + r) / 2
            cy = remapped_qubit

            hh = h / 2
            
            color = 'xy'
            if pulse_instance.pulse_type in [PulseType.CZ]:
                color = 'cz'
            elif pulse_instance.pulse_type in [PulseType.Z]:
                color = 'z'
            elif pulse_instance.pulse_type in [PulseType.RO_COMP, PulseType.RO]:
                color = 'read'
            
            rect_bg = plt.Rectangle((l, cy - hh), w, 2*hh,
                            facecolor="white", edgecolor="none",
                            alpha=1.0, zorder=2.3)
            ax.add_patch(rect_bg)
            rect = plt.Rectangle((l, cy - hh), w, 2*hh,
                                        facecolor=seq_color_map[color], edgecolor="none",
                                        alpha=0.2, zorder=2.5)
            ax.add_patch(rect)
            
            pad_x = max(0.02, w * 0.01)
            if pad_x * 2 >= w:  # 极窄块保护，防止 pad 过大
                pad_x = w * 0.49
            pad_y = hh * 0.02
            
            ax.hlines(cy - hh + pad_y, l + pad_x, r - pad_x,
                  colors=seq_color_map[color], alpha=0.5,
                  linewidth=2.2, zorder=2.6)
            ax.hlines(cy + hh- pad_y, l + pad_x, r - pad_x,
                    colors=seq_color_map[color], alpha=0.5,
                    linewidth=2.2, zorder=2.6)
            # 左/右边（各自往内 pad_x，y 方向也内缩 pad_y）
            ax.vlines(l + pad_x, cy - hh + pad_y, cy + hh - pad_y,
                    colors=seq_color_map[color], alpha=0.5, 
                    linewidth=2.2, zorder=2.6)
            ax.vlines(r - pad_x, cy - hh + pad_y, cy + hh - pad_y,
                    colors=seq_color_map[color], alpha=0.5,
                    linewidth=2.2, zorder=2.6)
            
            '''
            rect = plt.Rectangle((l, cy - h / 2), w, h,
                facecolor='white', edgecolor="C0", linewidth=3,
                zorder=2.5)
            '''
            if pulse_instance.pulse_type in [PulseType.CZ]:
                legend_flag[2] = rect
            elif pulse_instance.pulse_type in [PulseType.Z]:
                legend_flag[1] = rect
            elif pulse_instance.pulse_type in [PulseType.RO_COMP, PulseType.RO] :
                legend_flag[3] = rect
            else:
                legend_flag[0] = rect

            ax.add_patch(rect)

            name = f'{pulse_instance.pulse_type.value}'
            #if isinstance(pulse_instance, BasePulse):
            #    name += f'-{pulse_instance.shape}'
            text = plt.Text(cx, cy, name,
                verticalalignment='center', horizontalalignment='center',
                wrap=True, color='black',
                zorder=2.5)
            ax.add_artist(text)

            polygon_xy.append((cx, cy))

        if len(qubits) > 1:
            polygon = plt.Polygon(polygon_xy, closed = False,
                edgecolor='black', linewidth=3,
                zorder=2)
            ax.add_patch(polygon)

    ax.set_xlabel("time")

    if remap_time:
        padding_x = 3 / 5
        ax.set_xlim(0 - padding_x, len(moments_rmp2org) - 1 + padding_x)
    else:
        padding_x = (max(moments_rmp2org) - min(moments_rmp2org)) / 20
        ax.set_xlim(min(moments_rmp2org) - padding_x, max(moments_rmp2org) + padding_x)

    ax.grid(axis='x', color='grey', ls='--')
    if remap_time:
        ax.set_xticks(range(len(moments_rmp2org)))
    else:
        ax.set_xticks(moments_rmp2org)
    ax.set_xticklabels(moments_rmp2org)

    ax.set_ylabel("qubit")

    padding_y = 3 / 5
    ax.set_ylim(len(qubit_rmp2org) - 1 + padding_y, -padding_y)
    ax.grid(axis='y', color='black')
    ax.set_yticks(range(len(qubit_rmp2org)))
    ax.set_yticklabels(qubit_rmp2org)

    ax.set_frame_on(False)

    handles = []
    labels  = []
    if legend_flag[0] is not None: handles.append(legend_flag[0]); labels.append("xy")
    if legend_flag[1] is not None: handles.append(legend_flag[1]); labels.append("z")
    if legend_flag[2] is not None: handles.append(legend_flag[2]); labels.append("cz")
    if legend_flag[3] is not None: handles.append(legend_flag[3]); labels.append("readout")
    if legend_flag[4] is not None: handles.append(legend_flag[4]); labels.append("barrier")

    if handles:
        ax.legend(handles, labels, loc='upper left',
                  bbox_to_anchor=(0, 1.15), ncol=4, frameon=False)
    
    if to_base64:
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
        plt.close()  # 重要：关闭图表释放内存
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        return image_base64

def remap_to_indices(items, key=None):
    items_set = set(items)
    items_sorted = sorted(items_set, key=key)
    moments_dict = {t: i for i, t in enumerate(items_sorted)}

    org2rmp = moments_dict
    rmp2org = items_sorted
    return org2rmp, rmp2org


def iter_int_or_tuple(item):
    if isinstance(item, str | int): return [item]
    else:
        return item


channel_color_map = {
    "xy_": "C0", 
    "z_": "C2",
    "ro_": "C3",
}

def _color_for(ch: str) -> str:
    for k, c in channel_color_map.items():
        if ch.startswith(k):
            return c
    return "C7"

def plot_waveforms(out: dict[str, Wave],
                   channels: list[str] | None = None,
                   decimate_max: int = 200_000,
                   group_by_qubit: bool = False,
                   include_misc: bool = True,
                   qubit_regex: str = r".*_(\w+)$",
                   stacked=False,
                   to_base64=False):
    """
    out: 波形数据
    channels: 指定显示顺序；None 则按名字排序
    decimate_max: 点数太多时抽点画，加快绘制
    group_by_qubit: True 则按比特分组（每个比特的通道放到同一子图）
    include_misc: 分组时将未匹配到比特的通道放到 'misc' 组
    stacked: 同一比特不同通道数据是否合并在一个图里
    """
    if channels is None:
        channels = []
        for k in out:
            if out[k].use:
                channels.append(k)
        channels.sort()
        
    def _slice_xy(w: Wave):
        # 时间轴与该通道等长，再按 step 抽点
        y = w.data
        step = max(1, len(y) // decimate_max)
        tt = (np.arange(0, len(y), step, dtype=float) / w.srate) * 1e9  # ns
        yy = y[::step]
        return tt, yy

    if not group_by_qubit:
        # ------ 非分组模式：维持原行为，每通道一行 ------
        rows = len(channels)
        fig, axes = plt.subplots(rows, 1, sharex=True,
                                 figsize=(12, max(2.5, 1.6*rows)))
        if rows == 1:
            axes = [axes]

        for ax, ch in zip(axes, channels):
            w = out[ch]
            tt, yy = _slice_xy(w)
            ax.plot(tt, yy, lw=1.0, label=ch)
            ax.set_ylabel(ch, rotation=0, ha="right", va="center")
            ax.grid(True, which="both", alpha=0.3)

    else:
        # ------ 分组模式：按比特编号聚合 ------
        def extract_qubit(ch: str):
            m = re.match(qubit_regex, ch)
            return m.group(1) if m else None

        groups: dict[str, list[str]] = {}
        for ch in channels:
            q = extract_qubit(ch)
            if q is None:
                if include_misc:
                    groups.setdefault("misc", []).append(ch)
            else:
                name = f"q{q}"
                if q.startswith("B"): name = q
                groups.setdefault(name, []).append(ch)

        def sort_key(ch: str):
            if ch.startswith("z_"): return (0, ch)
            if ch.startswith("xy_"): return (1, ch)
            if ch.startswith("ro_"): return (2, ch)
            return (3, ch)

        for g in groups:
            groups[g].sort(key=sort_key)

        group_names = sorted([g for g in groups.keys() if g != "misc"])
        if include_misc and "misc" in groups:
            group_names.append("misc")

        rows = len(group_names)
        fig, axes = plt.subplots(rows, 1, sharex=True,
                                figsize=(12, max(2.8, 2.0*rows)))
        if rows == 1:
            axes = [axes]
            
        if not stacked:
            for ax, gname in zip(axes, group_names):
                chs = groups[gname]
                legends = []
                for ch in chs:
                    w = out[ch]
                    tt, yy = _slice_xy(w)
                    ln, = ax.plot(tt, yy, lw=1.0, color=_color_for(ch), label=ch)
                    legends.append(ln)
                ax.set_ylabel(gname, rotation=0, ha="right", va="center")
                ax.grid(True, which="both", alpha=0.3)
                if legends: ax.legend(handles=legends, loc="upper left", fontsize=8, frameon=False)
        else:
            for ax, gname in zip(axes, group_names):
                chs = groups[gname]
                    
                for lane, ch in enumerate(chs):
                    w = out[ch]
                    tt, yy = _slice_xy(w)

                    # 归一化到 [-0.4, +0.4]
                    denom = (np.max(np.abs(yy)) or 1.0)
                    y_norm = (yy / denom) * 0.4

                    # 累叠偏移：将通道波形抬到对应“车道”中心 (lane)
                    y_plot = lane + y_norm

                    ax.plot(tt, y_plot, lw=1.0, color=_color_for(ch), label=ch)

                ax.set_yticks(range(len(chs)))
                ax.set_yticklabels(chs)
                ax.set_ylim(-0.6, len(chs) - 1 + 0.6)
                ax.grid(True, axis="x", alpha=0.3)   # 只保留 x 方向网格
                ax.grid(False, axis="y")             # y 轴不需要数值网格
                ax.set_ylabel(gname, rotation=0, ha="right", va="center")
                
                # 底色
                for k in range(len(chs)):
                    if k % 2 == 1:  # 交替涂浅色
                        y0, y1 = k - 0.5, k + 0.5
                        #ax.fill_between(tt, y0, y1,
                        #                color="#000000", alpha=0.06, zorder=0.5)
                        ax.axhspan(y0, y1, xmin=0, xmax=1,
                        facecolor="#000000", alpha=0.06,
                        zorder=0.5, clip_on=False)
            
    axes[-1].set_xlabel("Time (ns)")
    plt.tight_layout()
    if to_base64:
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
        plt.close()  # 重要：关闭图表释放内存
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        return image_base64
    else:
        plt.show()


def confidence_ellipse(mean, cov, nstd=2.0):
    """
    在 IQ 平面画协方差椭圆。
    nstd=1,2,3 分别近似表示 1σ, 2σ, 3σ 椭圆。
    """
    vals, vecs = np.linalg.eigh(cov)
    order = vals.argsort()[::-1]
    vals = vals[order]
    vecs = vecs[:, order]

    angle = np.degrees(np.arctan2(vecs[1, 0], vecs[0, 0]))
    width, height = 2 * nstd * np.sqrt(vals)

    return width, height, angle

def analyze_singleshot(iq0, iq1, bins=100, use_lda=True, plot=True, to_base64=False):
    iq0 = np.asarray(iq0)
    iq1 = np.asarray(iq1)

    assert iq0.shape[1] == 2
    assert iq1.shape[1] == 2

    n0 = len(iq0)
    n1 = len(iq1)

    # 1. 均值和协方差
    mu0 = iq0.mean(axis=0)
    mu1 = iq1.mean(axis=0)

    cov0 = np.cov(iq0.T)
    cov1 = np.cov(iq1.T)

    # 2. 找投影轴
    if use_lda:
        # pooled covariance
        cov_pool = ((n0 - 1) * cov0 + (n1 - 1) * cov1) / (n0 + n1 - 2)
        w = np.linalg.solve(cov_pool, mu1 - mu0)
    else:
        w = mu1 - mu0

    w = w / np.linalg.norm(w)

    # 3. 投影
    x0 = iq0 @ w
    x1 = iq1 @ w

    # 保证 |0> 在左，|1> 在右
    if x0.mean() > x1.mean():
        w = -w
        x0 = -x0
        x1 = -x1

    # 4. 用经验 CDF 扫描阈值
    all_x = np.sort(np.concatenate([x0, x1]))
    thresholds = 0.5 * (all_x[:-1] + all_x[1:])

    x0_sorted = np.sort(x0)
    x1_sorted = np.sort(x1)

    cdf0 = np.searchsorted(x0_sorted, thresholds, side="right") / n0
    cdf1 = np.searchsorted(x1_sorted, thresholds, side="right") / n1

    d0_arr = cdf0
    d1_arr = 1 - cdf1

    contrast = d0_arr + d1_arr - 1
    fidelity = 0.5 * (d0_arr + d1_arr)

    idx = np.argmax(fidelity)

    threshold = thresholds[idx]
    d0 = d0_arr[idx]
    d1 = d1_arr[idx]
    readout_fidelity = fidelity[idx]
    readout_contrast = contrast[idx]

    # 5. 可选：对投影分布做一维高斯拟合，用来画平滑曲线
    m0, s0 = stats.norm.fit(x0)
    m1, s1 = stats.norm.fit(x1)
    
    width0, height0, angle0 = confidence_ellipse(mu0, cov0, nstd=2)
    width1, height1, angle1 = confidence_ellipse(mu1, cov1, nstd=2)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    ax = axes[0]
    blue_cmap = LinearSegmentedColormap.from_list(
        "white_blue", ["white", "tab:blue"]
    )
    orange_cmap = LinearSegmentedColormap.from_list(
        "white_orange", ["white", "tab:orange"]
    )
    
    ax.hist2d(iq0[:, 0], iq0[:, 1], bins=bins, cmap=blue_cmap, alpha=0.9)
    ax.hist2d(iq1[:, 0], iq1[:, 1], bins=bins, cmap=orange_cmap, alpha=0.7)
    
    ax.scatter(mu0[0], mu0[1], marker="o", s=60, label="|0> center")
    ax.scatter(mu1[0], mu1[1], marker="o", s=60, label="|1> center")
    ell0 = Ellipse(xy=mu0,width=width0,height=height0,angle=angle0,fill=False,linewidth=2, color="tab:blue")
    ax.add_patch(ell0)
    ell1 = Ellipse(xy=mu1,width=width1,height=height1,angle=angle1,fill=False,linewidth=2, color="tab:orange")
    ax.add_patch(ell1)
    # 判别边界：w · r = threshold
    perp = np.array([-w[1], w[0]])
    center_line = threshold * w
    t = np.linspace(-3, 3, 200)
    # 根据数据范围调整线的长度
    scale = np.max(np.ptp(np.vstack([iq0, iq1]), axis=0))
    line = center_line[None, :] + t[:, None] * scale * perp[None, :]
    ax.plot(line[:, 0], line[:, 1], "k--", linewidth=2)
    ax.set_xlabel("I")
    ax.set_ylabel("Q")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"d_|0> = {d0:.4f}\nd_|1> = {d1:.4f}")
    ax.legend()
    ax = axes[1]
    counts0, edges, _ = ax.hist(x0, bins=bins, alpha=0.5, label="|0>")
    counts1, edges, _ = ax.hist(x1, bins=bins, alpha=0.5, label="|1>")
    ax.axvline(threshold, color="k", linewidth=2)
    ax.set_xlabel("Projection Axis")
    ax.set_ylabel("Count")
    ax2 = ax.twinx()
    grid = np.linspace(
        min(x0.min(), x1.min()),
        max(x0.max(), x1.max()),
        1000
    )
    # 高斯拟合 CDF
    cdf0_fit = stats.norm.cdf(grid, m0, s0)
    cdf1_fit = stats.norm.cdf(grid, m1, s1)
    # 判别曲线：P0_left - P1_left
    contrast_fit = cdf0_fit - cdf1_fit
    ax2.plot(grid, cdf0_fit, label="CDF |0>")
    ax2.plot(grid, cdf1_fit, label="CDF |1>")
    ax2.plot(grid, contrast_fit, label="Contrast")
    ax2.set_ylabel("Probability")
    ax2.set_ylim(0, 1.05)
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")
    ax.set_title(f"F = {readout_fidelity:.4f}, contrast = {readout_contrast:.4f}")
    plt.tight_layout()
        
    if plot:
        plt.show()
    
    if to_base64:
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
        plt.close()  # 重要：关闭图表释放内存
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        return image_base64
    
    return {
        "w": w,
        "threshold": threshold,
        "d0": d0,
        "d1": d1,
        "readout_fidelity": readout_fidelity,
        "readout_contrast": readout_contrast,
        "mu0": mu0,
        "mu1": mu1,
        "cov0": cov0,
        "cov1": cov1,
        "gaussian_fit_0": (m0, s0),
        "gaussian_fit_1": (m1, s1),
        
    }