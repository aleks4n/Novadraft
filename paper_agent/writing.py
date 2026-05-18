from paper_agent.methodology_composing_using_template import methodology_composing
from paper_agent.related_work_composing_using_template import related_work_composing
from paper_agent.experiments_composing import experiments_composing
from paper_agent.introduction_composing import introduction_composing
from paper_agent.conclusion_composing import conclusion_composing
from paper_agent.abstract_composing import abstract_composing
import asyncio
import argparse
from paper_agent.writing_fix import (
    clean_tex_files_in_folder,
    collect_cite_keys,
    collect_bib_from_folder,
    generate_bib_for_keys,
)
from paper_agent.tex_writer import compile_latex_project

async def writing(research_field: str, instance_id: str):
    await methodology_composing(research_field, instance_id)
    await related_work_composing(research_field, instance_id)
    await experiments_composing(research_field, instance_id)
    await introduction_composing(research_field, instance_id)
    await conclusion_composing(research_field, instance_id)
    await abstract_composing(research_field, instance_id)

    target_folder = f"{research_field}/target_sections/{instance_id}"
    clean_tex_files_in_folder(target_folder)

    bib_file_path = f'{research_field}/target_sections/{instance_id}/iclr2025_conference.bib'

    # Step 1: extract any @-style bib entries the LLM embedded in section files
    collect_bib_from_folder(target_folder, bib_file_path)

    # Step 2: collect all \cite{} keys and generate missing bib entries via LLM
    cite_keys = collect_cite_keys(target_folder)
    if cite_keys:
        await generate_bib_for_keys(cite_keys, bib_file_path, existing_bib_path=bib_file_path)

    project_directory = f'{research_field}/target_sections/{instance_id}'
    main_file = "iclr2025_conference.tex"
    compile_latex_project(project_directory, main_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research_field", type=str, default="vq")
    parser.add_argument("--instance_id", type=str, default="one_layer_vq")
    args = parser.parse_args()
    asyncio.run(writing(args.research_field, args.instance_id))
