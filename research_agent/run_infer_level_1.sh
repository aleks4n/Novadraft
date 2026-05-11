
current_dir=$(dirname "$(readlink -f "$0")")
cd $current_dir

set -a
source ../.env
set +a


# export COMPLETION_MODEL=openai/claude-3-5-sonnet-20241022
# export CHEEP_MODEL=openai/claude-3-5-haiku-20241022

COMPLETION_MODEL=openrouter/openai/gpt-oss-120b:free
CHEEP_MODEL=openrouter/openai/gpt-oss-120b:free

category=vq
instance_id=one_layer_vq

python run_infer_plan.py --instance_path ../benchmark/final/${category}/${instance_id}.json --container_name test_eval --task_level task1 --model $COMPLETION_MODEL --workplace_name workplace --cache_path cache --port 12380 --max_iter_times 0 --category ${category} 

