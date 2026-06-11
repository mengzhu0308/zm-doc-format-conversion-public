#!/usr/bin/env python3
"""从 Markdown 文件中提取表格并保存为 xlsx 或 csv。"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

# 允许行首空白的 markdown 表格；分块匹配避免跨表合并。
TABLE_PATTERN = re.compile(
    r'^[ \t]*\|[^\n]+\|[ \t]*\n'
    r'^[ \t]*\|[-:\s|]+\|[ \t]*\n'
    r'(?:^[ \t]*\|[^\n]+\|[ \t]*\n?)+',
    re.MULTILINE,
)

# 匹配 fenced code block（三个或更多反引号围起的整段）；用于剥离块内伪表格。
_FENCED_CODE_BLOCK = re.compile(
    r'^[ \t]*```[^\n]*\n.*?\n^[ \t]*```[ \t]*$',
    re.MULTILINE | re.DOTALL,
)

# 分隔行（表头与数据之间）：至少含一个 `-` 或 `:`
_SEPARATOR_LINE = re.compile(r'^[-:\s|]+$')

# 已知 I/O 异常；其余异常（TypeError / AttributeError / NameError 等编程错误，以及
# KeyError / ValueError 等 pandas 内部数据异常）应透传以便发现 bug
_KNOWN_SAVE_EXCEPTIONS = (OSError, csv.Error, PermissionError)


class DependencyMissingError(Exception):
    """依赖缺失时抛出的业务异常，便于 process_file 结构化捕获。"""

    def __init__(self, required: str, message: str) -> None:
        super().__init__(message)
        self.required = required
        self.message = message


def _log(verbose: bool, msg: str) -> None:
    """仅在 verbose 模式下向 stderr 打印详细日志，避免污染 --json 协议输出。"""
    if verbose:
        print(f'[verbose] {msg}', file=sys.stderr)


def _normalize_text(text: str) -> str:
    """行尾归一化：CRLF 与 CR 全部归为 LF，确保正则锚定在 CRLF 输入下也正确。"""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def parse_table(table_text: str):
    """解析单个 markdown 表格文本。"""
    lines = [line.strip() for line in table_text.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        return None

    # 验证分隔行：要求 lines[1] 至少含一个 `-` 或 `:`，否则视为非标准表格
    if not _SEPARATOR_LINE.match(lines[1]) or not re.search(r'[-:]', lines[1]):
        return None

    headers = [cell.strip() for cell in lines[0].split('|')[1:-1]]

    # headers 全为空字符串时，pd.DataFrame(rows, columns=['']) 写 xlsx 会得到空列名，
    # 多 sheet 时容易引起列名冲突；与无 --- 分隔行时同等待遇，返回 None
    if not any(headers):
        return None

    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        rows.append(cells)

    return {'headers': headers, 'rows': rows}


def extract_tables(text: str):
    """从 markdown 文本中提取所有表格。"""
    cleaned = _normalize_text(text)
    # 检测未闭合围栏：奇数个围栏行 → 视为残缺围栏，整段视为代码块内容并剥离
    # 围栏行可以是裸 ``` 或带语言标识（如 ```python / ```js）
    fence_lines = list(re.finditer(r'^[ \t]*```.*$', cleaned, re.MULTILINE))
    if len(fence_lines) % 2 == 1:
        first = fence_lines[0]
        # 从首行开始到文本末尾整段剥离；只保留围栏行之前的文本（连同其行末换行）
        # 剥离方式：把 first.start() 之后所有内容清空，但保留 first.start() 之前的内容
        # （含行末的 \n）
        cleaned = cleaned[: first.start()].rstrip('\n')
    # 正常成对围栏用 _FENCED_CODE_BLOCK 剥离
    cleaned = _FENCED_CODE_BLOCK.sub('', cleaned)
    tables = []
    for match in TABLE_PATTERN.finditer(cleaned):
        table = parse_table(match.group(0))
        if table:
            tables.append(table)
    return tables


def save_csv(tables, output_path):
    """保存为 CSV。多表格时每个表格一个文件，用 _table1, _table2 后缀。

    csv.QUOTE_MINIMAL 会让 csv 模块在必要时自动加双引号，保证纯数字单元在
    csv.reader / pandas / Excel 三个消费者下都得到正确文本，无需额外包装。
    """
    if len(tables) == 1:
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(tables[0]['headers'])
            for row in tables[0]['rows']:
                writer.writerow(row)
    else:
        base, _ = os.path.splitext(output_path)
        for i, table in enumerate(tables, 1):
            path = f"{base}_table{i}.csv"
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(table['headers'])
                for row in table['rows']:
                    writer.writerow(row)


def _import_pandas():
    """延迟导入 pandas，缺失时抛结构化业务异常而不是 SystemExit。"""
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DependencyMissingError(
            required='pandas, openpyxl',
            message='保存 xlsx 需要 pandas 与 openpyxl，请先安装: pip install "pandas>=2.0" "openpyxl>=3.1"',
        ) from exc
    return pd


def save_xlsx(tables, output_path):
    """保存为 XLSX，多表格时每个表格一个 sheet；所有单元格内容以文本格式保存。"""
    pd = _import_pandas()

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        for i, table in enumerate(tables, 1):
            sheet_name = f'Table{i}' if len(tables) > 1 else 'Sheet1'
            # 强制所有列以字符串类型写入，防止 pandas 对大数字做数值类型推断导致精度丢失
            df = pd.DataFrame(table['rows'], columns=table['headers'], dtype=str)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            # 将所有数据单元格的格式设为文本（'@'），确保 Excel 按文本显示
            worksheet = writer.sheets[sheet_name]
            for row in worksheet.iter_rows(
                min_row=2,
                max_row=worksheet.max_row,
                min_col=1,
                max_col=worksheet.max_column,
            ):
                for cell in row:
                    cell.number_format = '@'


def _resolve_output_path(md_path: Path, fmt: str, out_dir: str | None) -> Path:
    """计算最终输出路径。

    - out_dir 显式且为单文件模式：使用 ``{源目录名}__{stem}.{fmt}`` 防冲突。
    - out_dir 显式且为目录批量模式：同样加源目录名前缀。
    - out_dir 隐式（None）：单文件时与源同目录，目录时由调用方决定。
    """
    if out_dir is not None:
        prefix = md_path.parent.name
        out_path = Path(out_dir) / f'{prefix}__{md_path.stem}.{fmt}'
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    else:
        out_path = md_path.with_suffix(f'.{fmt}')
    return out_path


def process_file(md_path: Path, fmt='xlsx', out_dir=None, verbose: bool = False):
    """处理单个 Markdown 文件。"""
    if not md_path.exists():
        return {'status': 'missing_input', 'message': f'文件不存在: {md_path}'}

    if md_path.suffix.lower() not in ('.md', '.markdown'):
        return {'status': 'not_supported', 'message': f'不支持的文件类型: {md_path.suffix}'}

    try:
        text = md_path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return {'status': 'read_failed', 'message': f'读取文件失败: {exc}'}

    _log(verbose, f'read {md_path} ({len(text)} chars)')

    tables = extract_tables(text)
    _log(verbose, f'extracted {len(tables)} table(s) from {md_path.name}')

    if not tables:
        return {'status': 'no_tables', 'message': f'未找到表格: {md_path}'}

    out_path = _resolve_output_path(md_path, fmt, out_dir)

    try:
        if fmt == 'csv':
            save_csv(tables, out_path)
        else:
            save_xlsx(tables, out_path)
        _log(verbose, f'wrote {out_path} (fmt={fmt})')
        return {
            'status': 'success',
            'input': str(md_path),
            'output': str(out_path),
            'tables_count': len(tables),
        }
    except DependencyMissingError as exc:
        return {
            'status': 'dependency_missing',
            'message': exc.message,
            'required': exc.required,
        }
    except _KNOWN_SAVE_EXCEPTIONS as exc:
        # 仅捕获已知 I/O / 数据类异常；编程错误（TypeError 等）应当透传以便发现 bug
        return {'status': 'save_failed', 'message': str(exc)}


def process_dir(dir_path: Path, fmt='xlsx', out_dir=None, verbose: bool = False):
    """批量处理目录下的所有 .md / .markdown 文件。"""
    if not dir_path.is_dir():
        return [{'status': 'missing_input', 'message': f'目录不存在: {dir_path}'}]

    # 合并 .md 与 .markdown 后缀，避免合同不一致。
    md_files = sorted({*dir_path.glob('*.md'), *dir_path.glob('*.markdown')})
    if not md_files:
        return [{'status': 'empty_input_dir', 'message': f'目录中没有 .md 文件: {dir_path}'}]

    _log(verbose, f'found {len(md_files)} markdown file(s) in {dir_path}')

    if out_dir is None:
        out_dir = dir_path.parent / f"{dir_path.name}_md2excel"
        out_dir.mkdir(parents=True, exist_ok=True)

    return [process_file(f, fmt, out_dir, verbose=verbose) for f in md_files]


def main():
    parser = argparse.ArgumentParser(description='从 Markdown 提取表格并保存为 xlsx/csv')
    parser.add_argument('--path', required=True, help='输入 .md/.markdown 文件或目录')
    parser.add_argument('--format', choices=['xlsx', 'csv'], default='xlsx', help='输出格式')
    parser.add_argument('--output-dir', help='输出目录（默认与源文件同目录）')
    parser.add_argument('--json', action='store_true', help='JSON 格式输出')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出（写入 stderr，不污染 --json 协议）')
    args = parser.parse_args()

    path = Path(args.path)
    results = (
        [process_file(path, args.format, args.output_dir, verbose=args.verbose)]
        if path.is_file()
        else process_dir(path, args.format, args.output_dir, verbose=args.verbose)
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            status = r['status']
            if status == 'success':
                print(f"[success] {r['input']} -> {r['output']} ({r['tables_count']} 表格)")
            else:
                print(f"[{status}] {r.get('input', '')} {r.get('message', '')}")

        if len(results) > 1:
            success = sum(1 for r in results if r['status'] == 'success')
            print(f'\n总计: {len(results)} 文件, 成功 {success} 个')


if __name__ == '__main__':
    main()
