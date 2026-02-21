#!/usr/bin/env python3
"""AppWorld 刷榜脚本 - 灵雀自主进化版"""
import os
import json
import time
from pathlib import Path
from datetime import datetime

# AppWorld imports
from appworld import AppWorld, load_task_ids

# 配置
OUTPUT_DIR = Path("/home/ubuntu/projects/lingque/appworld_results")
OUTPUT_DIR.mkdir(exist_ok=True)

# 设置 AppWorld 数据路径
os.environ["APPWORLD_DATA_PATH"] = "/home/ubuntu/projects/lingque/data"

# 测试集
TEST_NORMAL = load_task_ids("test_normal")
TEST_CHALLENGE = load_task_ids("test_challenge")

print(f"测试集: test_normal={len(TEST_NORMAL)}, test_challenge={len(TEST_CHALLENGE)}")

# 成长记录
growth_log = []

def run_single_task(task_id: str) -> dict:
    """运行单个任务"""
    try:
        world = AppWorld(task_id=task_id)
        
        # 获取任务描述
        instruction = world.task.instruction
        
        # 这里需要调用 LLM 来解决任务
        # 暂时返回模拟结果
        result = {
            "task_id": task_id,
            "instruction": instruction[:200] + "..." if len(instruction) > 200 else instruction,
            "status": "pending_llm",
            "timestamp": datetime.now().isoformat()
        }
        
        world.close()
        return result
        
    except Exception as e:
        return {
            "task_id": task_id,
            "error": str(e),
            "status": "error",
            "timestamp": datetime.now().isoformat()
        }

def main():
    """主函数"""
    print(f"\n{'='*50}")
    print(f"AppWorld 刷榜开始 - {datetime.now()}")
    print(f"{'='*50}\n")
    
    # 先跑 test_normal 前10个作为基线测试
    test_sample = TEST_NORMAL[:10]
    
    results = []
    for i, task_id in enumerate(test_sample):
        print(f"[{i+1}/{len(test_sample)}] Running {task_id}...")
        result = run_single_task(task_id)
        results.append(result)
        print(f"  -> {result['status']}")
    
    # 保存结果
    output_file = OUTPUT_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存: {output_file}")
    print(f"成长记录: {len(growth_log)} 条")

if __name__ == "__main__":
    main()
