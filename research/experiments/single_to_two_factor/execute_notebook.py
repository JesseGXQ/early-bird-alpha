"""在独立 Jupyter 内核中执行唯一研究入口，并持续保存执行进度。"""
from pathlib import Path
import argparse
import os
import time
import nbformat
from nbclient import NotebookClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notebook', default='research/notebooks/202611/single-to-two-factor_research_standard.ipynb')
    parser.add_argument('--through', type=int, default=None, help='仅用于分阶段调试：执行至该单元格（含）。')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    os.chdir(root)
    path = root/args.notebook
    notebook = nbformat.reads(path.read_text(encoding='utf-8-sig'), as_version=4)
    client = NotebookClient(notebook, timeout=None, kernel_name='python3', resources={'metadata': {'path': str(root)}})
    started = time.perf_counter()
    with client.setup_kernel():
        for index, cell in enumerate(notebook.cells):
            if args.through is not None and index > args.through:
                break
            if cell.cell_type != 'code':
                continue
            print(f'单元格 {index}: {cell.source.splitlines()[0][:95]}', flush=True)
            try:
                client.execute_cell(cell, index)
            finally:
                nbformat.write(notebook, path)
            for output in cell.get('outputs', []):
                if output.output_type == 'stream':
                    text = output.get('text', '')
                    print(text[-1600:], end='' if text.endswith('\n') else '\n', flush=True)
    print(f'执行完成：{time.perf_counter()-started:.1f}s；{path}', flush=True)


if __name__ == '__main__':
    main()

