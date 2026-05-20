import os
import subprocess



# def compile_latex_project(project_dir, main_tex_file):
#     """
#     编译包含多个文件的LaTeX项目
    
#     Args:
#         project_dir (str): 项目目录的路径
#         main_tex_file (str): 主tex文件的名称
#     """
#     try:
#         # 切换到项目目录
#         original_dir = os.getcwd()
#         os.chdir(project_dir)
        
#         # 获取主文件的完整路径
#         main_file_path = os.path.join(project_dir, main_tex_file)
        
#         print(f"开始编译: {main_file_path}")
        
#         # 运行编译命令两次(处理目录和引用)
#         for i in range(2):
#             result = subprocess.run(
#                 ['pdflatex', '-interaction=nonstopmode', main_tex_file],
#                 capture_output=True,
#                 text=True
#             )
            
#             if result.returncode != 0:
#                 print(f"编译错误:\n{result.stderr}")
#                 print(f"编译错误:\n{result.stdout}")
#                 return False
        
#         # 检查PDF是否生成
#         pdf_file = os.path.splitext(main_tex_file)[0] + '.pdf'
#         if os.path.exists(pdf_file):
#             print(f"PDF生成成功: {os.path.join(project_dir, pdf_file)}")
#             return True
#         else:
#             print("PDF生成失败")
#             return False
            
#     except Exception as e:
#         print(f"发生错误: {str(e)}")
#         return False
        
#     finally:
#         # 恢复原始工作目录
#         os.chdir(original_dir)

# import os
# import subprocess

def run_and_print(args):
    result = subprocess.run(args, capture_output=True, text=True)
    print(f"[命令] {' '.join(args)}")
    print(result.stdout)
    print(result.stderr)
    return result

def compile_latex_project(project_dir, main_tex_file):
    try:
        original_dir = os.getcwd()
        os.chdir(project_dir)
        
        print(f"开始编译: {main_tex_file}")
        
        # Find pdflatex — check PATH first, then common TeX install locations
        pdflatex_cmd = 'pdflatex'
        try:
            subprocess.run(['which', 'pdflatex'], capture_output=True, check=True)
        except subprocess.CalledProcessError:
            import glob as _glob
            candidates = [
                '/Library/TeX/texbin/pdflatex',
                '/usr/texbin/pdflatex',
            ] + _glob.glob('/usr/local/texlive/*/bin/*/pdflatex')
            found = next((p for p in candidates if os.path.isfile(p)), None)
            if found:
                pdflatex_cmd = found
                print(f"Found pdflatex at: {pdflatex_cmd}")
            else:
                print("⚠️  警告: pdflatex 未安装，跳过 PDF 编译")
                print("💡 提示: 先安装 BasicTeX: brew install --cask basictex")
                print("   然后重启终端或运行: eval \"$(/usr/libexec/path_helper)\"")
                print(f"✅ LaTeX 源文件已生成: {main_tex_file}")
                return True

        # Remove stale aux/bbl files to avoid conflicts with old bibtex runs
        base_name_clean = os.path.splitext(main_tex_file)[0]
        for ext in ('.aux', '.bbl', '.blg', '.log', '.out', '.toc'):
            try:
                os.remove(base_name_clean + ext)
            except FileNotFoundError:
                pass

        # 第一次 pdflatex
        result1 = subprocess.run([pdflatex_cmd, '-interaction=nonstopmode', main_tex_file], capture_output=True, text=True)

        # bibtex（如果有引用）
        base_name = os.path.splitext(main_tex_file)[0]
        bibtex_cmd = os.path.join(os.path.dirname(pdflatex_cmd), 'bibtex') if os.path.dirname(pdflatex_cmd) else 'bibtex'
        if os.path.exists(base_name + ".aux"):
            bib_result = subprocess.run([bibtex_cmd, base_name], capture_output=True, text=True)
            if bib_result.returncode != 0 or 'error' in bib_result.stdout.lower():
                print(f"⚠️  BibTeX warnings/errors:\n{bib_result.stdout[:800]}")

        # 再运行两次 pdflatex
        result2 = subprocess.run([pdflatex_cmd, '-interaction=nonstopmode', main_tex_file], capture_output=True, text=True)
        result3 = subprocess.run([pdflatex_cmd, '-interaction=nonstopmode', main_tex_file], capture_output=True, text=True)
        # print(result2.stdout)
        # print(result2.stderr)
        # print()
        # print(result3.stdout)
        # print(result3.stderr)
        
        # 检查PDF
        pdf_file = base_name + '.pdf'
        if os.path.exists(pdf_file):
            print(f"✅ PDF生成成功: {os.path.join(project_dir, pdf_file)}")
            return True
        else:
            print("❌ PDF生成失败")
            return False
            
    except FileNotFoundError as e:
        if 'pdflatex' in str(e):
            print(f"⚠️  错误: pdflatex 未安装")
            print("💡 提示: 要安装 pdflatex，请运行:")
            print("   macOS: brew install basictex")
            print("   Linux: sudo apt-get install texlive-latex-base texlive-latex-extra")
            print("   Windows: 下载并安装 MiKTeX 或 TeX Live")
            print(f"✅ LaTeX 源文件已生成，可手动编译: {main_tex_file}")
            return True
        else:
            print(f"发生错误: {str(e)}")
            return False
        
    except Exception as e:
        print(f"发生错误: {str(e)}")
        return False
        
    finally:
        os.chdir(original_dir)

# 使用示例
# # project_directory = "/data2/tjb/writing_part/paper_agent/tex_project"
# # main_file = "main.tex"
# project_directory = "./paper_agent/final_paper"
# main_file = "iclr2025_conference.tex"
# compile_latex_project(project_directory, main_file)