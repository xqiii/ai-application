# 使用llama-factory 进行 QLoRA微调对话

## 安装

使用AutoDL 进行安装，脚本 install.sh

## 数据集

https://huggingface.co/datasets/neil-code/dialogsum-test

使用 `convert_dataset.py` 进行数据划分。

## 配置


1. 编写训练文件

`llama3_qlora_sft_bsd.yaml`

2. 执行训练

激活虚拟环境：`. finetune/bin/activate`

`USE_MODELSCOPE_HUB=1 llamafactory-cli train examples/train_lora/llama3_qlora_sft_bsd.yaml`


3. chat测试

编写接口文件： `llama3_qlora_sft_in.yaml`

执行 chat 服务

`USE_MODELSCOPE_HUB=1 llamafactory-cli chat examples/inference/llama3_qlora_sft_in.yaml`

4. 合并模型

编写合并模型文件：`llama3_qlora_sft_merge.yaml`

`USE_MODELSCOPE_HUB=1 llamafactory-cli export examples/merge_lora/llama3_qlora_sft_merge.yaml`

5. 部署为api

`USE_MODELSCOPE_HUB=1 llamafactory-cli api examples/inference/llama3_qlora_sft_in.yaml`