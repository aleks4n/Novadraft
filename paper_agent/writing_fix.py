import os
import re

def clean_tex_file(filepath):
    """Remove ``` fences, \\end{document}, and \\begin{thebibliography} blocks from section files."""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    while lines and '```' in lines[0]:
        lines.pop(0)
    while lines and '```' in lines[-1]:
        lines.pop(-1)

    # Strip lines that belong only in the main document, not in section files
    def _is_main_doc_only(line):
        s = line.strip()
        return (s == r'\end{document}'
                or s.startswith(r'\bibliography{')
                or s.startswith(r'\bibliographystyle{'))
    lines = [l for l in lines if not _is_main_doc_only(l)]

    # Strip \begin{thebibliography}...\end{thebibliography} blocks — handled by BibTeX
    cleaned = []
    in_bib = False
    for line in lines:
        if line.strip().startswith(r'\begin{thebibliography}'):
            in_bib = True
            continue
        if in_bib:
            if line.strip().startswith(r'\end{thebibliography}'):
                in_bib = False
            continue
        cleaned.append(line)

    # Fix cite keys containing spaces (BibTeX doesn't support them).
    # Handle multi-key cites like \cite{keyA, keyB} by fixing each key individually.
    def _fix_cite_content(content):
        parts = [k.strip().replace(' ', '_').lstrip('_').lower() for k in content.split(',')]
        return ','.join(parts)

    result = []
    for line in cleaned:
        if '\\cite{' in line:
            line = re.sub(r'\\cite\{([^}]*)\}',
                          lambda m: '\\cite{' + _fix_cite_content(m.group(1)) + '}',
                          line)
        result.append(line)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(result)

def clean_tex_files_in_folder(folder_path):
    for root, _, files in os.walk(folder_path):
        for file in files:
            if not file.endswith('.tex'):
                continue
            full_path = os.path.join(root, file)
            # Skip the main document — it must keep \end{document}
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    head = f.read(300)
                if r'\documentclass' in head:
                    continue
            except Exception:
                pass
            clean_tex_file(full_path)


def process_tex_file(tex_path, bib_output_path):
    with open(tex_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    bib_started = False
    bib_lines = []

    for i, line in enumerate(lines):
        # 删除以 \bib 开头的行
        if line.strip().startswith('\\bib'):
            # print(f"删除行（\\bib 开头）: {line.strip()}")
            continue

        # 查找第一个以 @ 开头的行，标志 bib 开始
        if not bib_started and line.strip().startswith('@'):
            bib_started = True
            # print(f"检测到 bib 条目开始于第 {i+1} 行")
            bib_lines.append(line)
        elif bib_started:
            bib_lines.append(line)
        else:
            new_lines.append(line)

    # 写回清理后的 .tex 文件
    with open(tex_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
        # print(f"更新后的 tex 文件已写入：{tex_path}")

    # 如果提取到了 bib 内容，写入 .bib 文件
    if bib_lines:
        with open(bib_output_path, 'w', encoding='utf-8') as f:
            f.writelines(bib_lines)
            # print(f"BibTeX 条目已写入：{bib_output_path}")
    else:
        # print("未找到任何以 @ 开头的 BibTeX 条目，未创建 .bib 文件。")
        print("all files are right")


def collect_cite_keys(folder_path):
    """Return all unique \\cite{} keys found across all .tex files in folder.
    Keys with spaces are normalised by replacing spaces with underscores."""
    keys = set()
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.tex'):
                try:
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        content = f.read()
                    for match in re.findall(r'\\cite\{([^}]+)\}', content):
                        for key in match.split(','):
                            keys.add(key.strip().replace(' ', '_').lstrip('_').lower())
                except Exception:
                    pass
    return keys


def collect_bib_from_folder(folder_path, bib_output_path):
    """Extract embedded @-style bib entries from ALL tex files in folder."""
    all_bib_lines = []
    seen_keys = set()
    for root, _, files in os.walk(folder_path):
        for file in sorted(files):
            if file.endswith('.tex'):
                full_path = os.path.join(root, file)
                with open(full_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                bib_started = False
                new_lines = []
                entry_lines = []
                for line in lines:
                    if line.strip().startswith('\\bib'):
                        continue
                    if not bib_started and line.strip().startswith('@'):
                        bib_started = True
                        entry_lines.append(line)
                    elif bib_started:
                        entry_lines.append(line)
                    else:
                        new_lines.append(line)
                if entry_lines:
                    all_bib_lines.extend(entry_lines)
                    with open(full_path, 'w', encoding='utf-8') as f:
                        f.writelines(new_lines)
    if all_bib_lines:
        with open(bib_output_path, 'w', encoding='utf-8') as f:
            f.writelines(all_bib_lines)
        return True
    return False


async def generate_bib_for_keys(cite_keys, bib_output_path, existing_bib_path=None):
    """Ask the LLM to generate bibtex entries for all citation keys."""
    import sys, os as _os
    sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from benchmark_collection.utils.openai_utils import GPTClient

    # Find which keys are already covered
    covered = set()
    if existing_bib_path and _os.path.exists(existing_bib_path):
        with open(existing_bib_path, 'r', encoding='utf-8') as f:
            bib_text = f.read()
        for m in re.finditer(r'@\w+\{(\S+?),', bib_text):
            covered.add(m.group(1).rstrip(',').strip())

    missing = sorted(cite_keys - covered)
    if not missing:
        return

    gpt_client = GPTClient()
    keys_list = '\n'.join(missing)
    prompt = f"""Generate complete and accurate BibTeX entries for the following citation keys.
Use the exact key name as provided (do not rename it).
Include real paper titles, full author lists, venue/journal, and year.
Use @article, @inproceedings, or @misc as appropriate.

Citation keys:
{keys_list}

Output only the BibTeX entries, nothing else."""

    bib_content = await gpt_client.chat(prompt=prompt)
    if not bib_content:
        return

    # Append to existing bib or write new
    mode = 'a' if existing_bib_path and _os.path.exists(existing_bib_path) else 'w'
    with open(bib_output_path, mode, encoding='utf-8') as f:
        if mode == 'a':
            f.write('\n')
        f.write(bib_content)