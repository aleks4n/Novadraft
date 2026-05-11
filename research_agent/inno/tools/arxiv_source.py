import urllib.parse
import feedparser
import time
import requests
import re
import tarfile
import os
from typing import List
def search_arxiv(query, max_results=10):
    """
    Search arXiv papers
    """
    base_url = "http://export.arxiv.org/api/query?"

    params = {
        "search_query": f"(ti:{query} OR abs:{query})",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }

    query_url = base_url + urllib.parse.urlencode(params)

    response = feedparser.parse(query_url)

    papers = []

    for entry in response.entries:
        pdf_url = None
        for link in entry.links:
            if getattr(link, "type", "") == "application/pdf":
                pdf_url = link.href
                break

        paper = {
            "title": entry.get("title", ""),
            "author": [a.name for a in entry.get("authors", [])],
            "published": entry.get("published", ""),
            "summary": entry.get("summary", ""),
            "url": entry.get("link", ""),
            "pdf_url": pdf_url,
        }

        papers.append(paper)

        time.sleep(0.2)  # lighter rate limit

    return papers

def extract_tex_content(tar_path, ):
    """
    从tar.gz文件中提取所有.tex文件的内容
    
    参数:
        tar_path: tar.gz文件路径
    
    返回:
        str: 所有.tex文件内容的拼接，格式为 filename + content
    """
    try:
        all_content = []
        
        with tarfile.open(tar_path, 'r:gz') as tar:
            # 获取所有.tex文件
            tex_files = [f for f in tar.getmembers() if f.name.endswith('.tex')]
            
            for tex_file in tex_files:
                # 提取文件内容
                f = tar.extractfile(tex_file)
                if f is not None:
                    try:
                        # 尝试以utf-8解码
                        content = f.read().decode('utf-8')
                    except UnicodeDecodeError:
                        # 如果utf-8失败，尝试latin-1
                        f.seek(0)
                        content = f.read().decode('latin-1')
                    
                    # 添加文件名和内容
                    all_content.append(f"\n{'='*50}\nFilename: {tex_file.name}\n{'='*50}\n")
                    all_content.append(content)
                    all_content.append("\n\n")
        
        # 将所有内容拼接成一个字符串
        return "".join(all_content)
    
    except Exception as e:
        return f"Extract failed with error: {str(e)}"

def download_arxiv_source(arxiv_url, local_root, workplace_name, title: str):
    """
    download arxiv paper source file
    
    Args:
        arxiv_url: arxiv paper url, e.g. 'http://arxiv.org/abs/2006.11239v2'
        local_root: local root directory
        workplace_name: workplace name
    """
    try:
        # 从URL中提取论文ID
        paper_id = re.search(r'abs/([^/]+)', arxiv_url).group(1)
        
        # 构建source URL
        source_url = f'http://arxiv.org/src/{paper_id}'
        
        # 发送请求
        response = requests.get(source_url)
        
        # 检查状态码
        if response.status_code == 200:
            try: 
                paper_src_dir = os.path.join(local_root, workplace_name, "paper_source")
                os.makedirs(paper_src_dir, exist_ok=True)
                filepath = os.path.join(paper_src_dir, f"{title.replace(' ', '_').lower()}.tar.gz")
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                tex_content = extract_tex_content(filepath)
                paper_tex_dir = os.path.join(local_root, workplace_name, "papers")
                os.makedirs(paper_tex_dir, exist_ok=True)
                with open(os.path.join(paper_tex_dir, f"{title.replace(' ', '_').lower()}.tex"), 'w') as f:
                    f.write(tex_content)
                return {"status": 0, "message": f"Download paper '{title}' successfully", "path": f"/{workplace_name}/papers/{title.replace(' ', '_').lower()}.tex"}
            except Exception as e:
                return {"status": -1, "message": f"Download paper '{title}' failed with error: {str(e)}", "path": None}
        else:
            return {"status": -1, "message": f"Download paper '{title}' failed with HTTP status code {response.status_code}", "path": None}
            
    except Exception as e:
        return {"status": -1, "message": f"Download paper '{title}' failed with error: {str(e)}", "path": None}

def download_arxiv_source_by_title(paper_list: List[str], local_root: str, workplace_name: str):
    """
    download arxiv paper source file by title
    
    Args:
        title: paper title
        paper_dir: paper directory
    """
    ret_msg = []
    for title in paper_list:
        papers = search_arxiv(title, max_results=1)
        if len(papers) == 0:
            ret_msg.append(f"Cannot find the paper '{title}' in arxiv")
            continue
        paper = papers[0]
        download_info =  download_arxiv_source(paper['url'], local_root, workplace_name, title)
        if download_info["status"] == -1:
            ret_msg.append(download_info["message"])
        else:
            ret_msg.append(download_info["message"] + f"\nThe paper is downloaded to path: {download_info['path']}")
    return "\n".join(ret_msg)
    