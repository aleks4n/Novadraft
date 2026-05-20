import os
import json
import asyncio
import logging
from tqdm import tqdm
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark_collection.utils.openai_utils import GPTClient
from paper_agent.section_composer import SectionComposer, setup_logging
import shutil

class AbstractComposer(SectionComposer):
    def __init__(self, research_field: str, structure_iterations: int = 2):
        super().__init__(research_field, "abstract", structure_iterations)

    async def generate_title(self, content: str) -> str:
        prompt = f"""Generate a concise, specific academic paper title based on the following paper content.

Content:
{content[:3000]}

Requirements:
- Specific and descriptive (not generic)
- Reflects the core method and contribution
- Academic style, suitable for a top ML venue
- 8-15 words

Output only the title, no quotes, no punctuation at the end."""
        title = await self.gpt_client.chat(prompt=prompt)
        return (title or "Research Paper").strip().strip('"').strip("'")

    def _create_minimal_iclr_template(self, dst_dir: str, title: str = "Research Paper"):
        """Create a minimal ICLR 2025 conference LaTeX template."""
        main_tex = (
            r"""\documentclass{article}
\usepackage[final]{iclr2025_conference}
\usepackage{lmodern}
\usepackage{hyperref}
\usepackage{url}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{microtype}
\usepackage{natbib}

\title{"""
            + title
            + r"""}

\author{AI-Researcher}

\begin{document}

\maketitle

\input{abstract}

\input{introduction}

\input{methodology}

\input{experiments}

\input{related_work}

\input{conclusion}

\bibliography{iclr2025_conference}
\bibliographystyle{plainnat}

\end{document}
"""
        )
        # Minimal style file placeholder
        style_content = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{iclr2025_conference}[2024/01/01 ICLR 2025 Style]
\DeclareOption{final}{}
\DeclareOption{accepted}{}
\DeclareOption{preprint}{}
\DeclareOption*{}
\ProcessOptions\relax
\usepackage{geometry}
\geometry{margin=1in}
"""
        # Minimal bib file
        bib_content = r"""@article{vqvae,
  title={Neural Discrete Representation Learning},
  author={van den Oord, Aaron and Vinyals, Oriol and Kavukcuoglu, Koray},
  journal={arXiv preprint arXiv:1711.00937},
  year={2017}
}
@article{vqgan,
  title={Taming Transformers for High-Resolution Image Synthesis},
  author={Esser, Patrick and Rombach, Robin and Ommer, Bjorn},
  journal={CVPR},
  year={2021}
}
"""
        with open(os.path.join(dst_dir, 'iclr2025_conference.tex'), 'w') as f:
            f.write(main_tex)
        with open(os.path.join(dst_dir, 'iclr2025_conference.sty'), 'w') as f:
            f.write(style_content)
        with open(os.path.join(dst_dir, 'iclr2025_conference.bib'), 'w') as f:
            f.write(bib_content)
        logging.info(f"Created minimal ICLR template in {dst_dir}")

    def read_section_content(self, target_paper: str, section_name: str) -> str:
        """Read content from an existing section file"""
        normalized_title = self.normalize_title(target_paper)
        section_path = f"{self.research_field}/target_sections/{normalized_title}/{section_name}.tex"
        try:
            with open(section_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logging.warning(f"Section file {section_path} not found")
            return ""

    async def generate_or_revise_structure(self, content, current_structure, iteration):
        prompt = f"""Based on the given content, generate or revise the abstract structure, using latex format.
Current iteration: {iteration}/{self.structure_iterations}

Current structure (if exists):
{current_structure}

Content to analyze:
{content}

Guidelines for structure generation:
1. STRUCTURE FORMAT:
   \begin{{abstract}}
   
   % [Problem Context]
   % - Research area and problem statement
   % - Current challenges or limitations
   
   % [Proposed Solution]
   % - Main approach and key innovations
   % - Technical highlights
   
   % [Results and Impact]
   % - Key experimental findings
   % - Practical significance
   
   \end{{abstract}}

2. CONTENT REQUIREMENTS:
   - Keep structure focused and concise
   - Ensure logical flow from problem to results
   - Highlight key contributions
   - Emphasize practical impact

Output only the LaTeX structure with comments as specified above."""

        return await self.gpt_client.chat(prompt=prompt)

    async def detailize_subsection(self, structure, current_text, content):
        writing_template = self.get_random_template()
        prompt = f"""Write a comprehensive abstract based on the provided structure and content.

CURRENT ABSTRACT VERSION (if any):
{current_text}

STRUCTURE:
{structure}

PAPER CONTENT TO SUMMARIZE:
{content}

Writing Guidelines:

1. PROBLEM CONTEXT:
   - State research area and problem clearly
   - Shortly recap existing research line
   - Research motivation
   - Present specific problem
   - Highlight key challenges

2. PROPOSED SOLUTION:
   - Present main approach
   - Highlight technical innovations and its relation to motivation

3. RESULTS:
   - State primary findings

STYLE REQUIREMENTS:
- Use precise, clear language
- Maintain formal academic tone
- Avoid technical jargon
- Focus on key points only
- Keep to 150-250 words
- Write as one cohesive paragraph
- No citations or references
- No future work mentions
- Use latex, don't use markdown

Output the complete LaTeX text for the abstract section. Do not output any other content."""

        return await self.gpt_client.chat(prompt=prompt)

    async def final_writing_checklist(self, abstract_text: str) -> str:
        prompt = f"""Review and revise the abstract section following these guidelines:

Current abstract text:
{abstract_text}

CHECKLIST FOR REVISION:

1. CONTENT COMPLETENESS:
   - Clear problem statement
   - Main methodology explained
   - Key results included
   - Significance highlighted

2. WRITING QUALITY:
   - No redundant information
   - Clear logical flow
   - Concise presentation
   - Appropriate length

3. TECHNICAL ACCURACY:
   - Precise terminology
   - Correct technical claims
   - Clear methodology description
   - Accurate result reporting

4. STYLE AND FORMAT:
   - Professional academic tone
   - No jargon or undefined terms
   - Single paragraph structure
   - No citations or references
   - No future work mentions

Output the revised abstract section incorporating all these improvements. Reply with LaTeX text only."""

        return await self.gpt_client.chat(prompt=prompt)

    async def compose_section(self, target_paper: str) -> str:
        checkpoint_dir = self.get_checkpoint_path(target_paper)
        os.makedirs(checkpoint_dir, exist_ok=True)

        # Read existing sections
        introduction = self.read_section_content(target_paper, "introduction")
        methodology = self.read_section_content(target_paper, "methodology")
        experiments = self.read_section_content(target_paper, "experiments")
        content_bundle = introduction + '\n\n' + methodology + '\n\n' + experiments

        # Step 1: Iterative structure generation
        structure = ""
        structure_checkpoint = self.load_checkpoint(target_paper, "structure")
        
        if structure_checkpoint:
            structure = structure_checkpoint["final_structure"]
            logging.info("Loaded structure from checkpoint")
        else:
            for iteration in range(self.structure_iterations):
                logging.info(f"Structure iteration {iteration + 1}/{self.structure_iterations}")
                structure = await self.generate_or_revise_structure(
                    content_bundle, structure, iteration + 1)
                self.write_temp_log(structure, f"iteration_{iteration+1}_final")
            
            self.save_checkpoint(target_paper, "structure", {
                "final_structure": structure
            })

        # Step 2: Write complete abstract
        final_abstract = await self.detailize_subsection(structure, "", content_bundle)
        self.write_temp_log(final_abstract, "initial_abstract")

        
        # Save final output
        output_dir = f"{self.research_field}/target_sections/{self.normalize_title(target_paper)}"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "abstract.tex")


        dst_dir = output_dir
        src_dir = "./paper_agent/final_paper"
        if os.path.isdir(src_dir):
            for filename in os.listdir(src_dir):
                src_path = os.path.join(src_dir, filename)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, dst_dir)
        else:
            logging.warning(f"final_paper directory not found at {src_dir}. Creating minimal template.")
            paper_title = await self.generate_title(content_bundle)
            logging.info(f"Generated paper title: {paper_title}")
            self._create_minimal_iclr_template(dst_dir, title=paper_title)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(final_abstract)
        logging.info(f"Saved final abstract to {output_path}")

        return final_abstract

async def abstract_composing(research_field: str, instance_id: str):
    setup_logging(research_field)
    
    composer = AbstractComposer(research_field=research_field, structure_iterations=2)
    # target_paper = 'Heterogeneous Graph Contrastive Learning for Recommendation'
    
    try:
        abstract = await composer.compose_section(instance_id)
        logging.info("Abstract composition completed")
    except Exception as e:
        logging.error(f"Error during abstract composition: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(abstract_composing())