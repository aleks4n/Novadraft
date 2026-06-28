import json
import sys
from pathlib import Path
from typing import Any

try:
	from src.llm.registry import get_llm
except ModuleNotFoundError:
	# Allow direct script execution from the repository root or nested folders.
	repo_root = Path(__file__).resolve().parents[3]
	if str(repo_root) not in sys.path:
		sys.path.insert(0, str(repo_root))
	from src.llm.registry import get_llm


def collect_gap_strings(paper_json_path: str | Path) -> str:
	paper_path = Path(paper_json_path)
	paper_data = json.loads(paper_path.read_text(encoding="utf-8"))
	gap_ids = paper_data.get("gaps", {}).get("gap_ids", [])
	gap_dir = paper_path.parents[2] / "gaps" / "pending"

	gap_strings: list[str] = []
	for gap_id in gap_ids:
		gap_path = gap_dir / f"{gap_id}.json"
		if gap_path.exists():
			gap_strings.append(gap_path.read_text(encoding="utf-8").strip())

	return "\n".join(gap_strings)


def collect_gap_objects(paper_json_path: str | Path) -> list[dict[str, Any]]:
	paper_path = Path(paper_json_path)
	paper_data = json.loads(paper_path.read_text(encoding="utf-8"))
	gap_ids = paper_data.get("gaps", {}).get("gap_ids", [])
	gap_dir = paper_path.parents[2] / "gaps" / "pending"

	gap_objects: list[dict[str, Any]] = []
	for gap_id in gap_ids:
		gap_path = gap_dir / f"{gap_id}.json"
		if not gap_path.exists():
			continue
		try:
			gap_objects.append(json.loads(gap_path.read_text(encoding="utf-8")))
		except json.JSONDecodeError:
			continue

	return gap_objects


def ensure_master_gap_list() -> Path:
	master_gap_list_path = Path(__file__).resolve().parent / "master_gap_list.json"
	if not master_gap_list_path.exists():
		master_gap_list_path.write_text("[]\n", encoding="utf-8")
		print(f"Created {master_gap_list_path.name}")
	return master_gap_list_path


def _response_to_text(response: Any) -> str:
	if isinstance(response, str):
		return response

	content = getattr(response, "content", response)
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		parts: list[str] = []
		for item in content:
			if isinstance(item, str):
				parts.append(item)
			elif isinstance(item, dict) and "text" in item:
				parts.append(str(item["text"]))
		return "\n".join(parts)

	return str(content)


def _gaps_are_similar(
	llm: Any | None,
	incoming_gap: dict[str, Any],
	existing_gap: dict[str, Any],
	incoming_gaps_text: str,
	master_gaps_text: str,
) -> bool:
	prompt = (
		"You are checking whether two research gaps describe the same idea. "
		"Compare only title and canonical_description semantics. "
		"Return ONLY strict JSON: {\"similar\": true} or {\"similar\": false}.\n\n"
		f"Incoming gaps raw string:\n{incoming_gaps_text}\n\n"
		f"Master gaps raw string:\n{master_gaps_text}\n\n"
		f"Gap A (incoming):\n{json.dumps(incoming_gap, ensure_ascii=False)}\n\n"
		f"Gap B (master):\n{json.dumps(existing_gap, ensure_ascii=False)}\n"
	)

	if llm is None:
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)

	try:
		response_text = _response_to_text(llm.invoke(prompt)).strip()
		parsed = json.loads(response_text)
		return bool(parsed.get("similar", False))
	except Exception:
		# Deterministic fallback if model output is unavailable or malformed.
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)


def _merge_evidence(existing_gap: dict[str, Any], incoming_gap: dict[str, Any]) -> None:
	existing_evidence = existing_gap.setdefault("evidence", [])
	if not isinstance(existing_evidence, list):
		existing_evidence = []
		existing_gap["evidence"] = existing_evidence

	incoming_evidence = incoming_gap.get("evidence", [])
	if not isinstance(incoming_evidence, list):
		return

	existing_serialized = {json.dumps(item, sort_keys=True) for item in existing_evidence}
	for evidence_item in incoming_evidence:
		serialized = json.dumps(evidence_item, sort_keys=True)
		if serialized not in existing_serialized:
			existing_evidence.append(evidence_item)
			existing_serialized.add(serialized)


def _load_master_gaps(master_gap_list_path: Path) -> list[dict[str, Any]]:
	try:
		loaded = json.loads(master_gap_list_path.read_text(encoding="utf-8"))
	except json.JSONDecodeError:
		return []

	if isinstance(loaded, list):
		return [item for item in loaded if isinstance(item, dict)]
	return []


def _safe_get_reasoner() -> Any | None:
	try:
		return get_llm("reasoner")
	except Exception:
		return None


def main(paper_json_path: str) -> str:
	master_gap_list_path = ensure_master_gap_list()
	all_gaps_text = collect_gap_strings(paper_json_path)
	incoming_gaps = collect_gap_objects(paper_json_path)
	master_gaps = _load_master_gaps(master_gap_list_path)
	llm = _safe_get_reasoner()

	if not master_gaps:
		
		master_gap_list_path.write_text(
			json.dumps(incoming_gaps, indent=2, ensure_ascii=False) + "\n",
			encoding="utf-8",
		)
		print(f"Initialized {master_gap_list_path.name} with {len(incoming_gaps)} gap(s)")
		return all_gaps_text

	master_gaps_text = json.dumps(master_gaps, ensure_ascii=False, indent=2)
	for incoming_gap in incoming_gaps:
		matched = False
		for existing_gap in master_gaps:
			if _gaps_are_similar(llm, incoming_gap, existing_gap, all_gaps_text, master_gaps_text):
				print(
					"Similar gaps recognized: "
					f"incoming={incoming_gap.get('gap_id', '<unknown>')} -> "
					f"master={existing_gap.get('gap_id', '<unknown>')}"
				)
				_merge_evidence(existing_gap, incoming_gap)
				matched = True
				break
		if not matched:
			master_gaps.append(incoming_gap)

	master_gap_list_path.write_text(
		json.dumps(master_gaps, indent=2, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)
	return all_gaps_text


if __name__ == "__main__":
	import sys

	if len(sys.argv) < 2:
		raise SystemExit("Usage: python agent.py path/to/paper.json")

	main(sys.argv[1])
