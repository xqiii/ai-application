"""
将 neil-code/dialogsum-test 数据集转换为 LlamaFactory alpaca 格式的 JSON 文件。
复用 QLoRA notebook 中的 create_prompt_formats 逻辑。
"""

import json
import argparse
from pathlib import Path
from datasets import load_dataset


INTRO_BLURB = "Below is an instruction that describes a task. Write a response that appropriately completes the request."
INSTRUCTION_KEY = "Please Summarize the below conversation."


def create_prompt_formats(sample: dict) -> dict:
    """
    将原始样本转换为 LlamaFactory alpaca 格式。
    字段: instruction, input, output

    与 QLoRA notebook 中的 create_prompt_formats 保持相同的 prompt 结构,
    但拆分为 instruction / input / output 三个字段以适配 LlamaFactory。
    """
    instruction = f"{INTRO_BLURB}\n{INSTRUCTION_KEY}"
    input_text = sample["dialogue"] if sample.get("dialogue") else ""
    output_text = sample["summary"] if sample.get("summary") else ""

    return {
        "instruction": instruction,
        "input": input_text,
        "output": output_text,
    }


def convert_split(dataset_split) -> list[dict]:
    return [create_prompt_formats(sample) for sample in dataset_split]


def main():
    parser = argparse.ArgumentParser(description="将 dialogsum 数据集转换为 LlamaFactory alpaca JSON 格式")
    parser.add_argument(
        "--data_path",
        type=str,
        default="../QLoRA/neil-code_dialogsum-test",
        help="HuggingFace 数据集本地路径或名称",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="./data",
        help="输出目录",
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["train", "validation", "test"],
        help="要转换的数据集 split",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"加载数据集: {args.data_path}")
    dataset = load_dataset(args.data_path)
    print(f"数据集信息: {dataset}")

    for split in args.splits:
        if split not in dataset:
            print(f"跳过不存在的 split: {split}")
            continue

        records = convert_split(dataset[split])
        output_file = output_dir / f"dialogsum_{split}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        print(f"[{split}] {len(records)} 条样本 -> {output_file}")

    print("\n示例 (train[0]):")
    if "train" in dataset:
        sample = create_prompt_formats(dataset["train"][0])
        print(json.dumps(sample, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
