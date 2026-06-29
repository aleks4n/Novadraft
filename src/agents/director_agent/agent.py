import json
import os
from functools import lru_cache
from math import sqrt
from pathlib import Path
from typing import Any

try:
	import torch
	from transformers import AutoModel, AutoTokenizer
except ModuleNotFoundError:
	torch = None
	AutoModel = None
	AutoTokenizer = None


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


def _gap_embedding_text(gap: dict[str, Any]) -> str:
	title = str(gap.get("title", "")).strip()
	description = str(
		gap.get("canonical_description") or gap.get("description") or ""
	).strip()
	parts: list[str] = []
	if title:
		parts.append(f"Title: {title}")
	if description:
		parts.append(f"Description: {description}")
	return "\n".join(parts).strip()


def _cosine_distance(left: list[float], right: list[float]) -> float:
	if len(left) != len(right) or not left:
		return 1.0

	dot_product = sum(a * b for a, b in zip(left, right))
	left_norm = sqrt(sum(value * value for value in left))
	right_norm = sqrt(sum(value * value for value in right))
	if not left_norm or not right_norm:
		return 1.0

	return 1.0 - (dot_product / (left_norm * right_norm))


class _LocalQwenEmbeddings:
	def __init__(self, model_name: str, max_length: int) -> None:
		if torch is None or AutoModel is None or AutoTokenizer is None:
			raise ModuleNotFoundError("Local embedding dependencies are unavailable")

		self.max_length = max_length
		self.device = "mps" if torch.backends.mps.is_available() else "cpu"
		dtype = torch.float16 if self.device == "mps" else torch.float32
		self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
		self.model = AutoModel.from_pretrained(
			model_name,
			trust_remote_code=True,
			torch_dtype=dtype,
		)
		self.model.to(self.device)
		self.model.eval()

	def _encode(self, texts: list[str]) -> list[list[float]]:
		inputs = self.tokenizer(
			texts,
			padding=True,
			truncation=True,
			max_length=self.max_length,
			return_tensors="pt",
		)
		inputs = {key: value.to(self.device) for key, value in inputs.items()}

		with torch.inference_mode():
			outputs = self.model(**inputs)

		hidden_states = outputs.last_hidden_state
		mask = inputs["attention_mask"].unsqueeze(-1).to(hidden_states.dtype)
		summed = (hidden_states * mask).sum(dim=1)
		counts = mask.sum(dim=1).clamp(min=1e-9)
		embeddings = summed / counts
		embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
		return [vector.detach().cpu().tolist() for vector in embeddings]

	def embed_documents(self, texts: list[str]) -> list[list[float]]:
		return self._encode(texts)


@lru_cache(maxsize=1)
def _safe_get_local_qwen_embeddings() -> Any | None:
	if torch is None or AutoModel is None or AutoTokenizer is None:
		return None

	model_name = os.getenv("NOVADRAFT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
	max_length = int(os.getenv("NOVADRAFT_EMBEDDING_MAX_LENGTH", "512"))

	try:
		return _LocalQwenEmbeddings(model_name=model_name, max_length=max_length)
	except Exception:
		return None


def _gaps_are_similar(
	incoming_gap: dict[str, Any],
	existing_gap: dict[str, Any],
) -> bool:
	left_text = _gap_embedding_text(incoming_gap)
	right_text = _gap_embedding_text(existing_gap)
	if not left_text or not right_text:
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)

	embeddings = _safe_get_local_qwen_embeddings()
	if embeddings is None:
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)

	try:
		left_vector, right_vector = embeddings.embed_documents([left_text, right_text])
		distance = _cosine_distance(left_vector, right_vector)
		threshold = float(os.getenv("NOVADRAFT_GAP_COSINE_DISTANCE_THRESHOLD", "0.18"))
		return distance <= threshold
	except Exception:
		# Deterministic fallback if the embedding endpoint is unavailable or malformed.
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


def main(paper_json_paths: list[str]) -> str:
	master_gap_list_path = ensure_master_gap_list()
	master_gaps = _load_master_gaps(master_gap_list_path)
	all_gaps_text_parts: list[str] = []

	for paper_json_path in paper_json_paths:
		print(f"\nProcessing: {paper_json_path}")
		all_gaps_text = collect_gap_strings(paper_json_path)
		incoming_gaps = collect_gap_objects(paper_json_path)
		all_gaps_text_parts.append(all_gaps_text)

		if not master_gaps and not all_gaps_text_parts[0]:
			# First paper, first call to main - initialize
			master_gap_list_path.write_text(
				json.dumps(incoming_gaps, indent=2, ensure_ascii=False) + "\n",
				encoding="utf-8",
			)
			print(f"Initialized {master_gap_list_path.name} with {len(incoming_gaps)} gap(s)")
			master_gaps = incoming_gaps
		else:
			# Process incoming gaps from this paper against current master
			for incoming_gap in incoming_gaps:
				matched = False
				for existing_gap in master_gaps:
					if _gaps_are_similar(incoming_gap, existing_gap):
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

	# Save the final master gap list after processing all papers
	master_gap_list_path.write_text(
		json.dumps(master_gaps, indent=2, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)
	return "\n".join(all_gaps_text_parts)


if __name__ == "__main__":
	import sys

	if len(sys.argv) < 2:
		raise SystemExit("Usage: python agent.py path/to/paper1.json [path/to/paper2.json ...]")

	paper_paths = sys.argv[1:]
	main(paper_paths)
