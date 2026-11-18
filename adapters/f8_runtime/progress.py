"""Exact acquired sweep-row progress; independent of wall-time estimates."""
from math import prod


class SweepProgress:
    def __init__(self, sweep_setting, native_repeats=1, job_index=1, job_count=1):
        if type(native_repeats) is not int or native_repeats < 1:
            raise ValueError('native_repeats must be a positive integer')
        if not 1 <= job_index <= job_count:
            raise ValueError('invalid one-based job index/count')
        self.axis_lengths = {str(name): len(values) for name, values in sweep_setting.items()}
        if not self.axis_lengths or any(count < 1 for count in self.axis_lengths.values()):
            raise ValueError('all actual sweep axes must have at least one point')
        self.sweep_rows = prod(self.axis_lengths.values())
        self.total_rows = self.sweep_rows * native_repeats
        self.native_repeats = native_repeats
        self.job_index, self.job_count = job_index, job_count
        self.completed_rows = 0

    def state(self):
        fraction = self.completed_rows / self.total_rows
        return {
            'rowNumber': self.completed_rows,
            'completedRows': self.completed_rows,
            'totalRows': self.total_rows,
            'sweepRows': self.sweep_rows,
            'sweepAxisLengths': self.axis_lengths,
            'nativeRepeats': self.native_repeats,
            'jobIndex': self.job_index,
            'jobCount': self.job_count,
            'jobProgress': 100 * fraction,
            'progress': 100 * (self.job_index - 1 + fraction) / self.job_count,
            'progressBasis': 'equal_weight_jobs_with_actual_acquired_rows_within_current_job',
            'message': f'任务 {self.job_index}/{self.job_count} · 已采集扫描点 {self.completed_rows}/{self.total_rows}',
        }

    def acquired(self):
        if self.completed_rows >= self.total_rows:
            raise ValueError('acquisition callback count exceeds frozen sweep Cartesian product')
        self.completed_rows += 1
        return self.state()

    def complete(self):
        if self.completed_rows != self.total_rows:
            raise ValueError('completed acquisition callbacks do not cover the frozen sweep Cartesian product')
        return self.state()
