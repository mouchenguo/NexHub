"""内存优化（Windows EmptyWorkingSet 实现）。

通过回收进程工作集降低物理内存占用。部分系统进程需要管理员权限，
权限不足时会跳过并返回统计，不影响其他进程。
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable

import psutil

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100


class MemoryOptimizer:
    @staticmethod
    def stats() -> dict:
        memory = psutil.virtual_memory()
        return {
            "total": memory.total,
            "used": memory.used,
            "available": memory.available,
            "percent": memory.percent,
            "process_count": len(psutil.pids()),
        }

    def optimize(self, progress: Callable[[int, str], None] | None = None) -> dict:
        if sys.platform != "win32":
            raise RuntimeError("内存优化功能仅支持 Windows")
        before = psutil.virtual_memory().used
        started = time.perf_counter()
        processes = list(psutil.process_iter(["pid", "name"]))
        optimized = 0
        skipped = 0
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        total = max(1, len(processes))

        for index, process in enumerate(processes, start=1):
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, process.pid
            )
            if not handle:
                skipped += 1
            else:
                try:
                    if psapi.EmptyWorkingSet(handle):
                        optimized += 1
                    else:
                        skipped += 1
                finally:
                    kernel32.CloseHandle(handle)
            if progress and index % 12 == 0:
                progress(int(index / total * 90), f"正在处理进程 {index}/{total}")

        if progress:
            progress(96, "正在统计释放结果...")
        time.sleep(0.35)
        after = psutil.virtual_memory().used
        elapsed = time.perf_counter() - started
        if progress:
            progress(100, "优化完成")
        return {
            "before": before,
            "after": after,
            "freed": max(0, before - after),
            "optimized": optimized,
            "skipped": skipped,
            "process_count": len(processes),
            "elapsed": elapsed,
            "stats": self.stats(),
        }
