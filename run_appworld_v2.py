#!/usr/bin/env python3
"""AppWorld 刷榜脚本 - 灵雀自主进化版 v2
集成 Anthropic API 实际执行任务
"""
import os
import json
import time
import anthropic
from pathlib import Path
from datetime import datetime

# AppWorld imports
from appworld import AppWorld, load_task_ids

# 配置
OUTPUT_DIR = Path("/home/ubuntu/projects/lingque/appworld_results")
OUTPUT_DIR.mkdir(exist_ok=True)

# 设置 AppWorld 数据路径
os.environ["APPWORLD_DATA_PATH"] = "/home/ubuntu/projects/lingque/data"

# 加载环境变量
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/projects/lingque/.env")

# 初始化 Anthropic 客户端（通过智谱代理）
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    base_url=os.getenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
)

# 测试集
TEST_NORMAL = load_task_ids("test_normal")
TEST_CHALLENGE = load_task_ids("test_challenge")

print(f"测试集: test_normal={len(TEST_NORMAL)}, test_challenge={len(TEST_CHALLENGE)}")

# 成长记录
growth_log = []

SYSTEM_PROMPT = """你是一个能够操作各种应用程序的智能助手。
用户会给你一个任务，你需要通过调用可用的 API 来完成任务。

你需要：
1. 理解任务目标
2. 调用正确的 API
3. 处理返回结果
4. 继续执行直到任务完成

请直接给出你的解决方案，包括具体的 API 调用。"""

def run_single_task(task_id: str) -> dict:
    """运行单个任务，调用 LLM 解决"""
    try:
        world = AppWorld(task_id=task_id)
        
        # 获取任务描述和可用 API
        instruction = world.task.instruction
        available_apis = world.task.api_docs  # 可用的 API 文档
        
        # 构建 prompt
        user_message = f"""任务: {instruction}

可用的 APIs:
{available_apis[:3000]}  # 限制长度避免 token 超限

请分析任务并给出解决方案。如果需要调用 API，请直接给出具体的调用代码。"""

        # 调用 LLM
        start_time = time.time()
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        elapsed = time.time() - start_time
        
        llm_response = response.content[0].text
        
        # 尝试执行 LLM 的解决方案
        # AppWorld 提供了一个执行环境
        try:
            # 获取任务的 ground truth 代码作为参考格式
            # 然后让 LLM 的输出在环境中执行
            execution_result = world.execute(llm_response)
            
            result = {
                "task_id": task_id,
                "instruction": instruction[:200] + "..." if len(instruction) > 200 else instruction,
                "llm_response": llm_response[:500],
                "execution_result": str(execution_result)[:500],
                "status": "completed",
                "elapsed_seconds": elapsed,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as exec_err:
            # 如果直接执行失败，尝试其他方式
            result = {
                "task_id": task_id,
                "instruction": instruction[:200] + "..." if len(instruction) > 200 else instruction,
                "llm_response": llm_response[:500],
                "execution_error": str(exec_err),
                "status": "execution_failed",
                "elapsed_seconds": elapsed,
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
        time.sleep(1)  # 避免 rate limit
    
    # 保存结果
    output_file = OUTPUT_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # 统计
    completed = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] != "completed")
    
    print(f"\n结果已保存: {output_file}")
    print(f"完成: {completed}/{len(results)}, 失败: {failed}")
    print(f"成长记录: {len(growth_log)} 条")

if __name__ == "__main__":
    main()
